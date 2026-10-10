# 评测（§阶段2「评测体系正式化」）。
#
# 为什么值得进产品而不是留个脚本：客户问"你凭什么叫企业级、凭什么说更准"时，
# 需要一份能反复跑、能看历史、数字可比的东西。脚本做不到"改完参数立刻看有没有退化"。
#
# 逐题走**与问答端点同一条链路**（同一 retrieve、同一 chat_fn、同一份配置快照）——
# 否则评测证明的不是线上效果。
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import Runtime, client_ip, get_session, now, require_admin_role, require_license
from app.api.schemas import (ERR, ERR_BODY, ERR_GATE, EvalQuestionIn, EvalQuestionOut,
                             EvalQuestionPatchIn, EvalRunDetailOut, EvalRunIn, EvalRunOut,
                             EvalReportOut, PathId, QueryInt)
from app.api.serializers import item_json, question_json, run_json
from app.models import (EvalItemResult, EvalQuestion, EvalRun, KnowledgeBase, ModelConfig,
                        UsageRecord, User)
from app.services.audit import record as audit_record
from app.services.citations import parse_citations
from app.services.evaluation import aggregate, check_item, render_report
from app.services.judge import judge_stats
from app.services.retrieval import retrieve
from app.services.text import clean_text


def _active_embedding_model(session: Session) -> str | None:
    """网关**实际会用到**的向量模型：按 (fallback_rank, id) 取第一个启用的登记行
    （与 gateway.providers 的取用顺序一致）。没登记（纯 .env 直连）时返回 None。"""
    row = (session.query(ModelConfig).filter_by(scenario="embedding", enabled=True)
           .order_by(ModelConfig.fallback_rank, ModelConfig.id).first())
    return row.model_name if row else None


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def kb_scope_text(session: Session, kb_ids: list[int] | None) -> str:
        if kb_ids is None:
            return "全部知识库"
        rows = (session.query(KnowledgeBase).filter(KnowledgeBase.id.in_(kb_ids))
                .order_by(KnowledgeBase.id).all()) if kb_ids else []
        # 库删了留孤儿 id（EvalRun.kb_ids 是 JSONB 无 FK）：报告要如实说"当时评的那个库没了"
        return "、".join(k.name for k in rows) or "已删除的知识库"

    def execute_eval(run_id: int) -> None:
        """后台执行一轮评测。

        逐题走**与问答端点同一条链路**（同一 retrieve、同一 chat_fn、同一份配置快照）——
        否则评测证明的不是线上效果。每题单独落库 + 单独记账（评测是真花钱的调用），
        所以前端轮询能看到进度、进程被杀也只丢当前这一题。
        单题异常只记在该题身上（stage0 教训：一轮 17/20 里三题败在生成调用而非检索，
        报告必须能区分这两类）；只有整轮性异常才把 run 标 failed——绝不让前端永远转圈。
        """
        try:
            with Session(rt.engine) as session:
                run = session.get(EvalRun, run_id)
                if run is None:
                    return
                c = rt.cfg.effective()
                # 记账归属：单库评测记在该库上（用量视图按库看成本），多库/全库记 NULL
                usage_kb_id = run.kb_ids[0] if run.kb_ids and len(run.kb_ids) == 1 else None
                results: list[dict] = []
                used_models: set[str] = set()
                questions = (session.query(EvalQuestion).filter_by(enabled=True)
                             .order_by(EvalQuestion.id).all())
                for q in questions:
                    t0 = time.monotonic()
                    answer: str | None = None
                    cited_docs: list[str] = []
                    top_docs: list[str] = []
                    hits: list[dict] = []
                    err: str | None = None
                    try:
                        hits = retrieve(session, q.question, embedder=rt.embedder,
                                        kb_ids=run.kb_ids, allowed_kb_ids=None, rerank=rt.rerank,
                                        recall_k=c["recall_k"], top_k=c["rerank_top_n"],
                                        min_sim=c["min_sim"])
                        top_docs = [h["doc_name"] for h in hits]
                        if hits and rt.chat_fn is not None:
                            out = rt.chat_fn(q.question, hits)
                            answer = clean_text(out["answer"])   # 模型输出也过规范化闸（否则 NUL 直接 500）
                            cited_docs = parse_citations(answer, hits)
                            if out.get("model"):
                                used_models.add(out["model"])
                            session.add(UsageRecord(
                                tenant_id="default", user_email=run.created_by or "eval@local",
                                kb_id=usage_kb_id, scenario="chat",
                                model=out.get("model") or rt.settings.chat_model,
                                prompt_tokens=out.get("prompt_tokens") or 0,
                                completion_tokens=out.get("completion_tokens") or 0,
                                latency_ms=out.get("latency_ms")))
                        else:
                            # 没配模型或没命中：走未命中兜底并如实记为"没过"——不假装跑过模型
                            answer = c["chat_miss_answer"]
                    except Exception as exc:
                        err = f"{exc.__class__.__name__}: {exc}"
                    checks = check_item({"expect_all": q.expect_all, "expect_any": q.expect_any,
                                         "cites": q.cites},
                                        answer=answer, cited_docs=cited_docs, top_docs=top_docs)
                    # 裁判只判"真发生过生成"的题（有命中且有回答）：对未命中兜底话术打 faithfulness
                    # 没有意义（那句话本来就不是从资料里生成的），硬判只会往指标里灌噪声
                    verdict: dict | None = None
                    if run.judge and rt.judge_fn is not None and hits and answer and err is None:
                        try:
                            verdict = rt.judge_fn(q.question, answer, hits)
                            session.add(UsageRecord(
                                tenant_id="default", user_email=run.created_by or "eval@local",
                                kb_id=usage_kb_id, scenario="chat",
                                model=(verdict.get("model") or rt.settings.chat_model),
                                prompt_tokens=verdict.get("prompt_tokens") or 0,
                                completion_tokens=verdict.get("completion_tokens") or 0,
                                latency_ms=verdict.get("latency_ms")))
                        except Exception as exc:   # 裁判挂了不该让这道题的确定性判据失效
                            verdict = None
                            err = err or f"裁判调用失败：{exc.__class__.__name__}: {exc}"
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    session.add(EvalItemResult(
                        run_id=run.id, question_id=q.id, question=q.question,
                        category=q.category, note=q.note, expect_all=q.expect_all,
                        expect_any=q.expect_any, cites=q.cites, answer=answer,
                        cited_docs=cited_docs, top_docs=top_docs, checks=checks,
                        judge=verdict, passed=checks["passed"], rank=checks["rank"],
                        latency_ms=latency_ms, error=err))
                    results.append({**checks, "category": q.category, "cites": q.cites,
                                    "latency_ms": latency_ms, "judge": verdict})
                    run.total = len(results)
                    run.passed = sum(1 for r in results if r["passed"])
                    session.commit()
                metrics = aggregate(results)
                metrics["judge"] = judge_stats(results)
                run.metrics, run.total, run.passed = metrics, metrics["total"], metrics["passed"]
                run.chat_model = sorted(used_models)[0] if used_models else None
                # 向量模型取**网关实际会用到的那家**（按 fallback_rank,id 取第一个启用的），
                # 而不是 .env 里的默认值：真机上后者与后台登记的完全不是一回事，评测页那栏会误导人
                # （实测显示 qwen3.7-text-embedding，而真正在用的是 text-embedding-v4）。
                # 没有登记（纯 .env 直连）时才回落到配置值。
                run.embedding_model = (_active_embedding_model(session)
                                       or (rt.settings.embedding_model if rt.embedder is not None else None))
                run.status, run.finished_at = "done", now()
                session.commit()
        except Exception as exc:   # 整轮性故障：连不上库、配置读炸等——留证据，别静默
            logging.getLogger("umax").exception("评测执行失败 run=%s", run_id)
            try:
                with Session(rt.engine) as session:
                    run = session.get(EvalRun, run_id)
                    if run is not None:
                        run.status, run.error = "failed", f"{exc.__class__.__name__}: {exc}"
                        run.finished_at = now()
                        session.commit()
            except Exception:      # 兜底失败就只能靠日志了，绝不把异常再抛进后台线程
                logging.getLogger("umax").exception("评测失败态回写也失败 run=%s", run_id)

    @router.get("/api/v1/eval/questions", response_model=list[EvalQuestionOut],
                responses={**ERR_GATE})
    def list_eval_questions(admin: User = Depends(require_admin_role),
                            session: Session = Depends(get_session)):
        return [question_json(q) for q in
                session.query(EvalQuestion).order_by(EvalQuestion.id)]

    @router.post("/api/v1/eval/questions", status_code=201, response_model=EvalQuestionOut,
                 responses={**ERR_BODY, **ERR_GATE})
    def create_eval_question(body: EvalQuestionIn, request: Request,
                             admin: User = Depends(require_license),
                             session: Session = Depends(get_session)):
        q = EvalQuestion(tenant_id="default", question=body.question,
                         expect_all=body.expect_all, expect_any=body.expect_any,
                         cites=body.cites, category=body.category, note=body.note,
                         enabled=body.enabled)
        session.add(q)
        session.flush()
        audit_record(session, "eval_question_created", user_email=admin.email,
                     target_type="eval_question", target_id=q.id,
                     detail={"category": q.category}, ip=client_ip(request))
        session.commit()
        return question_json(q)

    @router.patch("/api/v1/eval/questions/{qid}", response_model=EvalQuestionOut,
                  responses={**ERR(404, "金标准题不存在"), **ERR_BODY, **ERR_GATE})
    def patch_eval_question(qid: PathId, body: EvalQuestionPatchIn, request: Request,
                            admin: User = Depends(require_license),
                            session: Session = Depends(get_session)):
        q = session.get(EvalQuestion, qid)
        if not q:
            raise HTTPException(404, "金标准题不存在")
        # 只处理真正带上的字段且非 null（缺席/显式 null 都=不动）：改标尺必须留痕
        fields = [k for k in body.model_fields_set if getattr(body, k) is not None]
        for k in fields:
            setattr(q, k, getattr(body, k))
        if fields:
            audit_record(session, "eval_question_updated", user_email=admin.email,
                         target_type="eval_question", target_id=q.id,
                         detail={"fields": sorted(fields)}, ip=client_ip(request))
            session.commit()
        return question_json(q)

    @router.delete("/api/v1/eval/questions/{qid}", status_code=204,
                   responses={**ERR(404, "金标准题不存在"), **ERR_GATE})
    def delete_eval_question(qid: PathId, request: Request,
                             admin: User = Depends(require_license),
                             session: Session = Depends(get_session)):
        """删题：历史运行的单题明细**不删**（question_id 置 NULL，快照还在）——
        评测记录是"当时的证据"，拿今天的尺子重判昨天的答案就失去可比性了。"""
        q = session.get(EvalQuestion, qid)
        if not q:
            raise HTTPException(404, "金标准题不存在")
        session.delete(q)
        audit_record(session, "eval_question_deleted", user_email=admin.email,
                     target_type="eval_question", target_id=qid,
                     detail={"category": q.category}, ip=client_ip(request))
        session.commit()

    @router.post("/api/v1/eval/runs", status_code=202, response_model=EvalRunOut,
                 responses={**ERR(400, "知识库 id 不存在、没有启用中的金标准题，或未配置裁判模型"),
                            **ERR_BODY, **ERR_GATE})
    def start_eval_run(body: EvalRunIn, request: Request,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        """起一轮评测并**立即返回**：20 题真模型要一两分钟，占着请求等会让浏览器/反代超时。
        返回 202 + run（status=running），前端轮询 /eval/runs/{id} 看进度。"""
        kb_ids = rt.valid_kb_ids(session, body.kb_ids)   # 与开放 API 同一套库存在性校验
        if not session.query(EvalQuestion).filter_by(enabled=True).count():
            raise HTTPException(400, "没有启用中的金标准题：先到金标准集里添加或启用")
        if body.judge and rt.judge_fn is None:
            raise HTTPException(400, "未配置裁判模型：先在模型页登记 chat 模型")
        run = EvalRun(tenant_id="default", status="running", kb_ids=kb_ids, judge=body.judge,
                      created_by=admin.email)
        session.add(run)
        session.flush()
        run_id = run.id
        audit_record(session, "eval_run_started", user_email=admin.email,
                     target_type="eval_run", target_id=run_id,
                     detail={"kb_ids": kb_ids}, ip=client_ip(request))
        session.commit()
        rt.spawn(lambda: execute_eval(run_id))
        session.refresh(run)     # 同步 spawn（测试）时已经跑完：回读真实状态再回给调用方
        return run_json(run)

    @router.get("/api/v1/eval/runs", response_model=list[EvalRunOut], responses={**ERR_GATE})
    def list_eval_runs(limit: QueryInt = 20, admin: User = Depends(require_admin_role),
                       session: Session = Depends(get_session)):
        # 上下界在端点内钳位（同 /audit：越界不是违法请求，不出 422 面）
        limit = max(1, min(limit, 100))
        return [run_json(r) for r in session.query(EvalRun)
                .order_by(EvalRun.id.desc()).limit(limit)]

    @router.get("/api/v1/eval/runs/{run_id}", response_model=EvalRunDetailOut,
                responses={**ERR(404, "评测记录不存在"), **ERR_GATE})
    def get_eval_run(run_id: PathId, admin: User = Depends(require_admin_role),
                     session: Session = Depends(get_session)):
        run = session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(404, "评测记录不存在")
        items = (session.query(EvalItemResult).filter_by(run_id=run_id)
                 .order_by(EvalItemResult.id).all())
        return {**run_json(run), "items": [item_json(i) for i in items]}

    @router.get("/api/v1/eval/runs/{run_id}/report", response_model=EvalReportOut,
                responses={**ERR(404, "评测记录不存在"), **ERR_GATE})
    def eval_report(run_id: PathId, admin: User = Depends(require_admin_role),
                    session: Session = Depends(get_session)):
        """Markdown 报告（JSON 里带 markdown 字符串）：交付文档要能贴，两次跑要能逐行 diff。
        有意不做成下载端点——media-type 面越小，契约越好守（同 /license 的取值思路）。"""
        run = session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(404, "评测记录不存在")
        items = (session.query(EvalItemResult).filter_by(run_id=run_id)
                 .order_by(EvalItemResult.id).all())
        markdown = render_report(
            meta={"time": (run.finished_at or run.started_at or now()).strftime("%Y-%m-%d %H:%M"),
                  "kb_scope": kb_scope_text(session, run.kb_ids),
                  "chat_model": run.chat_model, "embedding_model": run.embedding_model},
            metrics=run.metrics or {}, items=[item_json(i) for i in items])
        return {"markdown": markdown}

    @router.delete("/api/v1/eval/runs/{run_id}", status_code=204,
                   responses={**ERR(404, "评测记录不存在"), **ERR_GATE})
    def delete_eval_run(run_id: PathId, request: Request,
                        admin: User = Depends(require_license),
                        session: Session = Depends(get_session)):
        # 删历史要审计：删掉的正是"更准"的证据，动作本身得留痕
        run = session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(404, "评测记录不存在")
        session.delete(run)      # 单题明细走 FK CASCADE
        audit_record(session, "eval_run_deleted", user_email=admin.email,
                     target_type="eval_run", target_id=run_id,
                     detail={"passed": run.passed, "total": run.total}, ip=client_ip(request))
        session.commit()

    return router
