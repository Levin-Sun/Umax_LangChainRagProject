# 文档与入库：上传（单/批）、删除、预览切块、状态补丁、重处理。
#
# 这里集中了"原文件不可读"这一失败态的口径（read_or_fail）：四个入口（上传/批量/重处理/重建）
# 必须同形，否则同步档 500、异步档卡 pending，界面一直显示"排队中"而没有任何线索。
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.constants import BATCH_MAX
from app.api.deps import Runtime, allowed_kb_ids, client_ip, get_session, get_user, require_license
from app.api.schemas import (ERR, ERR_BODY, ERR_GATE, ERR_LOGIN_GATE, BatchUploadItemOut,
                             ChunkPreviewOut, DocOut, DocPatchIn, PathId)
from app.api.serializers import doc_json
from app.models import Chunk, Document, KnowledgeBase, User
from app.services.audit import record as audit_record
from app.services.ingest import ingest_document, read_stored, supported_ext
from app.services.text import clean_text


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def read_or_fail(session: Session, doc: Document) -> bytes | None:
        """读原始文件；读不出来就把文档标 failed 并返回 None。

        评审踩过：`read_stored` 抛在 ingest 的 try 之外，于是原文件被删/磁盘满/运维清过 uploads 时——
        同步档是未声明的 500、异步档是任务失败重试——而**文档行永远停在 pending**：界面一直"排队中"，
        没有线索指向"原文件没了"。失败态本身就是排查信息，四个入口（上传/批量/重处理/重建）口径必须一致。
        """
        try:
            return read_stored(doc)
        except OSError as exc:
            doc.status, doc.error = "failed", f"原文件不可读：{exc}"
            session.commit()
            return None

    def store_upload(session: Session, kb: KnowledgeBase, file: UploadFile,
                     request: Request, admin: User) -> tuple[Document | None, str, str | None]:
        """落盘 + 建 pending 文档 + 审计（不入库/不入队，由调用方决定后续）。返回 (doc, name, error)。

        修复⑧收口：multipart filename 不经 pydantic 验证链，是唯一的裸入口字符串——
        取 basename（防目录注入）+ 过 NUL/孤立代理清洗闸 + 截 200（doc_json 会回显给前端）。
        批量场景下"不支持的类型"只作逐项错误返回，不抛异常（一个坏文件不该拖垮整批）。
        """
        name = clean_text(Path(file.filename or "unnamed").name)[:200]
        raw = file.file.read()
        if not supported_ext(name):
            return None, name, f"暂不支持的文件类型：{name}"
        doc = Document(tenant_id="default", kb_id=kb.id, name=name, status="pending",
                       size_bytes=len(raw), mime=file.content_type)
        session.add(doc)
        session.flush()
        p = Path(rt.upload_dir) / f"{uuid4().hex[:8]}_{name}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        doc.storage_path = str(p)
        audit_record(session, "document_uploaded", user_email=admin.email,
                     target_type="document", target_id=doc.id,
                     detail={"kb_id": kb.id, "name": name}, ip=client_ip(request))
        return doc, name, None

    @router.post("/api/v1/kb/{kb_id}/documents/batch", status_code=201,
                 response_model=list[BatchUploadItemOut],
                 responses={**ERR(400, f"单批最多 {BATCH_MAX} 个文件"), **ERR(404, "知识库不存在"),
                            **ERR_BODY, **ERR_GATE})
    def upload_documents_batch(kb_id: PathId, request: Request,
                               files: list[UploadFile] = File(...),
                               admin: User = Depends(require_license),
                               session: Session = Depends(get_session)):
        """批量上传（§A）：逐文件给出结果，**部分成功**——坏文件只在自己那行报错。

        与单文件端点的差异（有意）：不支持的扩展名不再整批 415，而是该行 error 字段。
        """
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        if len(files) > BATCH_MAX:
            raise HTTPException(400, f"单批最多 {BATCH_MAX} 个文件")
        stored: list[tuple[Document, bytes]] = []
        results: list[dict] = []
        for f in files:
            doc, name, err = store_upload(session, kb, f, request, admin)
            if err or doc is None:
                results.append({"name": name, "document": None, "error": err})
                continue
            raw = read_or_fail(session, doc)
            if raw is None:            # 读不出来：这一项以 failed 形态返回，其余照常
                results.append({"name": name, "document": doc_json(doc), "error": None})
                continue
            stored.append((doc, raw))
            results.append({"name": name, "document": doc_json(doc), "error": None})
        session.commit()
        if rt.queue is not None:
            for doc, _raw in stored:
                rt.queue.enqueue_import(doc.id)
            return results                       # pending，worker 逐个接手
        # 同步模式：逐个入库并把结果回填到对应项（坏文件已经在上面的 results 里带着 error）
        done: dict[int, dict] = {}
        for doc, raw in stored:
            doc = ingest_document(session, doc, raw, embedder=rt.embedder, mineru=rt.mineru,
                                  vision_fn=rt.vision_fn,
                                  caption_images=rt.cfg.effective()["doc_image_caption"],
                                  **rt.chunk_params(kb))
            done[doc.id] = doc_json(doc)
        return [{**item,
                 "document": (done.get(item["document"]["id"], item["document"])
                              if item["document"] else None)}
                for item in results]

    @router.post("/api/v1/kb/{kb_id}/documents", status_code=201,
                 response_model=DocOut,
                 responses={**ERR(404, "知识库不存在"), **ERR(415, "不支持的文件类型"),
                            **ERR_BODY, **ERR_GATE})
    def upload_document(kb_id: PathId, request: Request, file: UploadFile = File(...),
                        admin: User = Depends(require_license),
                        session: Session = Depends(get_session)):
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        doc, name, err = store_upload(session, kb, file, request, admin)
        if err:
            raise HTTPException(415, f"暂不支持的文件类型：{name}（一期 .txt/.md，MinerU 接入后支持 PDF/Office）")
        assert doc is not None
        session.commit()
        if rt.queue is not None:
            rt.queue.enqueue_import(doc.id)
            return doc_json(doc)  # pending，worker 接手
        raw = read_or_fail(session, doc)
        if raw is None:
            return doc_json(doc)      # failed 带着原因回给前端，不是 500
        doc = ingest_document(session, doc, raw, embedder=rt.embedder, mineru=rt.mineru,
                              vision_fn=rt.vision_fn,
                              caption_images=rt.cfg.effective()["doc_image_caption"],
                              **rt.chunk_params(kb))
        return doc_json(doc)

    @router.delete("/api/v1/documents/{doc_id}", status_code=204,
                   responses={**ERR(404, "文档不存在"), **ERR_GATE})
    def delete_document(doc_id: PathId, request: Request,
                        admin: User = Depends(require_license),
                        session: Session = Depends(get_session)):
        """删文档：切块级联清、落盘原文件一并删（别把客户磁盘当垃圾场）。

        不可逆（要恢复得重新上传重建索引），故前端有二次确认；前端/接口的
        消息引用是历史快照，不随文档删除而变（当时的回答就该保持当时的出处）。
        """
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        name, kb_id, storage_path = doc.name, doc.kb_id, doc.storage_path
        session.delete(doc)          # chunks 走 FK CASCADE
        audit_record(session, "document_deleted", user_email=admin.email,
                     target_type="document", target_id=doc_id,
                     detail={"kb_id": kb_id, "name": name}, ip=client_ip(request))
        session.commit()
        if storage_path:
            try:
                Path(storage_path).unlink(missing_ok=True)
            except OSError as exc:   # 文件删不掉不该让接口失败：DB 已一致，残留文件无害
                import logging

                logging.getLogger("umax").warning("原文件清理失败 %s：%s", storage_path, exc)

    def visible_doc(session: Session, user: User, doc_id: int) -> Document:
        """按库级授权取文档：不可见（不存在/在未授权库）统一 404 同文案，探测不出差异。"""
        doc = session.get(Document, doc_id)
        allowed = allowed_kb_ids(session, user)
        if not doc or (allowed is not None and doc.kb_id not in allowed):
            raise HTTPException(404, "文档不存在")
        return doc

    @router.get("/api/v1/documents/{doc_id}", response_model=DocOut,
                responses={**ERR(404, "文档不存在"), **ERR_LOGIN_GATE})
    def get_document(doc_id: PathId, user: User = Depends(get_user),
                     session: Session = Depends(get_session)):
        return doc_json(visible_doc(session, user, doc_id))

    @router.get("/api/v1/documents/{doc_id}/chunks", response_model=list[ChunkPreviewOut],
                responses={**ERR(404, "文档不存在"), **ERR_LOGIN_GATE})
    def preview_chunks(doc_id: PathId, user: User = Depends(get_user),
                       session: Session = Depends(get_session)):
        visible_doc(session, user, doc_id)
        return [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content,
                 "has_embedding": c.embedding is not None, "meta": c.meta or {}}
                for c in session.query(Chunk).filter_by(document_id=doc_id)
                .order_by(Chunk.chunk_index)]

    @router.patch("/api/v1/documents/{doc_id}", response_model=DocOut,
                  responses={**ERR(404, "文档不存在"), **ERR_BODY, **ERR_GATE})
    def patch_document(doc_id: PathId, body: DocPatchIn,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        # 有意不记审计（spec §5 / 任务 5 表）：状态机内部推进（worker 回写 pending/ready/failed），
        # 不是人工写操作——记了只会淹没真实操作事件
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        if body.status:
            doc.status = body.status
        doc.error = body.error
        session.commit()
        return doc_json(doc)

    @router.post("/api/v1/documents/{doc_id}/reprocess", status_code=202, response_model=DocOut,
                 responses={**ERR(404, "文档不存在"), **ERR_GATE})
    def reprocess(doc_id: PathId, request: Request,
                  admin: User = Depends(require_license),
                  session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        # 审计只 add 不 commit：队列分支随下面的 commit 落库，同步分支随 ingest_document
        # 内部的 commit 落库——两条路径都是"操作真发生了才有事件"
        audit_record(session, "document_reprocessed", user_email=admin.email,
                     target_type="document", target_id=doc.id, detail={"kb_id": doc.kb_id},
                     ip=client_ip(request))
        if rt.queue is not None:
            doc.status, doc.error = "pending", None
            session.commit()
            rt.queue.enqueue_import(doc.id)
            return doc_json(doc)
        raw = read_or_fail(session, doc)
        if raw is None:
            return doc_json(doc)
        doc = ingest_document(session, doc, raw, embedder=rt.embedder,
                              mineru=rt.mineru, vision_fn=rt.vision_fn,
                              caption_images=rt.cfg.effective()["doc_image_caption"],
                              **rt.chunk_params(session.get(KnowledgeBase, doc.kb_id)))
        return doc_json(doc)

    return router
