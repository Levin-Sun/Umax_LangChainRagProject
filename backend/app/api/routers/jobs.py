# 重建索引（§3.3 风险对策「换 embedding 模型翻车」）+ 后台作业查询。
#
# 为什么需要重建：换 embedding 模型后旧向量与新查询不可比，全库必须从头算一遍；
# 客户"先传资料、后买 key"或中途换型都会撞上，一篇篇点「重试」不现实。
# **语义是"全量重来"而不是"只补缺失"**——可预测比省算力重要（按钮上也这么写）。
#
# 作业表与文档状态机分工：作业回答"谁为什么发起、最后成不成"（谁杀了进程也看得见），
# 文档状态机回答"每一篇跑到哪了"。两套进度必然漂移，所以只留一套真进度。
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import Runtime, client_ip, get_session, require_admin_role, require_license
from app.api.schemas import ERR, ERR_BODY, ERR_GATE, JobOut, QueryInt, ReindexIn, ReindexOut
from app.api.serializers import job_json
from app.models import BackgroundJob, Document, KnowledgeBase, User
from app.services.audit import record as audit_record
from app.services.ingest import ingest_document, read_stored
from app.services.jobs import create_job, finish_job, finish_progress


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def execute_reindex(doc_ids: list[int], job_id: int | None = None) -> None:
        """逐篇重建（各自独立 session 提交）。单篇失败只记在那篇身上——一篇坏文件不该让
        整库重建停在中途（与批量上传的"部分成功"同一口径）。整轮的异常只记日志：
        此时各篇状态已落库，前端看得到哪些成了、哪些没有。

        作业行负责"谁为什发了这件事、最后成不成"：逐篇更新 done/failed，结束时收尾——
        进程被杀时这一行留在 running，重启收尾会把它标成 interrupted（评审发现的盲区）。
        """
        c = rt.cfg.effective()
        done = failed = 0
        for doc_id in doc_ids:
            try:
                with Session(rt.engine) as session:
                    doc = session.get(Document, doc_id)
                    if doc is None:
                        continue         # 重建期间被删了：跳过，不是错误
                    kb = session.get(KnowledgeBase, doc.kb_id)
                    ingest_document(session, doc, read_stored(doc), embedder=rt.embedder,
                                    mineru=rt.mineru, vision_fn=rt.vision_fn,
                                    caption_images=c["doc_image_caption"],
                                    **rt.chunk_params(kb))
            except Exception as exc:     # 文件丢了/解析器炸了：只影响这一篇
                failed += 1
                logging.getLogger("umax").exception("重建索引失败 doc=%s", doc_id)
                try:
                    with Session(rt.engine) as s2:
                        d2 = s2.get(Document, doc_id)
                        if d2 is not None and d2.status != "ready":
                            # 错误文案与 ingest_document 的失败口径一致（同一处代码路径产出的东西要同形）
                            d2.status = "failed"
                            d2.error = f"{exc.__class__.__name__}: {exc}"
                            s2.commit()
                except Exception:
                    logging.getLogger("umax").exception("重建失败态回写也失败 doc=%s", doc_id)
            else:
                done += 1
            if job_id is not None:      # 逐篇落作业进度：重启后也能看出"跑到哪一篇"
                try:
                    with Session(rt.engine) as s3:
                        finish_progress(s3, job_id, done=done, failed=failed)
                except Exception:
                    logging.getLogger("umax").exception("作业进度回写失败 job=%s", job_id)
        if job_id is not None:
            with Session(rt.engine) as s4:
                finish_job(s4, s4.get(BackgroundJob, job_id), done=done, failed=failed)
                s4.commit()

    @router.post("/api/v1/reindex", status_code=202, response_model=ReindexOut,
                 responses={**ERR(400, "知识库不存在，或范围内没有可重建的文档"),
                            **ERR_BODY, **ERR_GATE})
    def reindex(body: ReindexIn, request: Request,
                admin: User = Depends(require_license),
                session: Session = Depends(get_session)):
        """一键重建索引：范围内文档**全部重新解析/切块/向量化**。

        为什么需要它：换 embedding 模型后旧向量与新查询不可比，全库必须从头算一遍；
        客户"先传资料、后买 key"或中途换型都会撞上，一篇篇点「重试」不现实。
        **语义是"全量重来"而不是"只补缺失"**——可预测比省算力重要（按钮上也这么写），
        想只补缺失的少数文档，用单篇「重试」即可。

        必须后台跑：同步档下几十上百篇要几分钟，占着请求必然被浏览器/反代掐断。
        进度就靠文档自己的状态机（pending→parsing→ready/failed）——前端已在轮询它，不另造一套进度。
        """
        kb_ids = rt.valid_kb_ids(session, body.kb_ids)
        q = session.query(Document)
        if kb_ids is not None:
            q = q.filter(Document.kb_id.in_(kb_ids))
        docs = q.order_by(Document.id).all()
        if not docs:
            raise HTTPException(400, "没有可重建的文档：先上传文档")
        # 状态先翻 pending 再返回：前端轮询立刻看到"排队中"，也不会把正在重建的库误显示成"就绪"
        for d in docs:
            d.status, d.error = "pending", None
        doc_ids = [d.id for d in docs]
        single = kb_ids[0] if kb_ids and len(kb_ids) == 1 else None
        # 作业登记：异步档是 queued（进度与收尾归 worker，backend 不谎称 running），
        # 同步档是 running（后台线程就在本进程里，进程没了它就没了 → 重启收尾会标 interrupted）
        job = create_job(session, kind="reindex", scope={"kb_ids": kb_ids},
                         total=len(doc_ids), created_by=admin.email,
                         status="queued" if rt.queue is not None else "running")
        job_id = job.id
        audit_record(session, "reindex_started", user_email=admin.email,
                     target_type="kb" if single else "reindex", target_id=single,
                     detail={"kb_ids": kb_ids, "documents": len(doc_ids), "job_id": job_id},
                     ip=client_ip(request))
        session.commit()
        if rt.queue is not None:
            for doc_id in doc_ids:       # 异步档交给 worker（同 reprocess 的口径）
                rt.queue.enqueue_import(doc_id)
        else:
            rt.spawn(lambda: execute_reindex(doc_ids, job_id))
        return {"documents": len(doc_ids), "kb_ids": kb_ids, "job_id": job_id}

    @router.get("/api/v1/jobs", response_model=list[JobOut], responses={**ERR_GATE})
    def list_jobs(limit: QueryInt = 20, admin: User = Depends(require_admin_role),
                  session: Session = Depends(get_session)):
        """后台作业列表（最近在前）。**与文档列表分工**：这里回答"谁为什么发起、最后成不成"，
        文档列表回答"每一篇跑到哪了"——两套进度必然漂移，所以只留一套真进度。"""
        limit = max(1, min(limit, 100))
        return [job_json(j) for j in session.query(BackgroundJob)
                .order_by(BackgroundJob.id.desc()).limit(limit)]

    return router
