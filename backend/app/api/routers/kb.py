# 知识库（§3.1）：建/列/删 + 库内文档列表。
# 可见性一律在**查询层**过滤而不是展示层隐藏——member 的空授权就应该是空列表。
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import (Runtime, allowed_kb_ids, client_ip, get_session, get_user,
                          require_license)
from app.api.schemas import ERR, ERR_BODY, ERR_GATE, ERR_LOGIN_GATE, DocOut, KbIn, KbOut, PathId
from app.api.serializers import doc_json
from app.models import Document, KnowledgeBase, User
from app.services.audit import record as audit_record


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/kb", status_code=201, response_model=KbOut,
                 responses={**ERR_BODY, **ERR_GATE})
    def create_kb(body: KbIn, request: Request,
                  admin: User = Depends(require_license),
                  session: Session = Depends(get_session)):
        # 建库时锁定切分参数（§3.3）：取配置中心的生效值快照，此后改全局配置不影响已建的库
        kb = KnowledgeBase(tenant_id="default", name=body.name, description=body.description,
                           embedding_model=rt.settings.embedding_model,
                           chunk_target=rt.cfg.effective()["chunk_target"])
        session.add(kb)
        session.flush()
        audit_record(session, "kb_created", user_email=admin.email,
                     target_type="kb", target_id=kb.id, detail={"name": body.name},
                     ip=client_ip(request))
        session.commit()
        return {"id": kb.id, "name": kb.name, "description": kb.description}

    @router.get("/api/v1/kb", response_model=list[KbOut], responses={**ERR_LOGIN_GATE})
    def list_kb(user: User = Depends(get_user), session: Session = Depends(get_session)):
        # member 的库列表在查询层过滤（不是前端隐藏）——空授权=空列表
        allowed = allowed_kb_ids(session, user)
        q = session.query(KnowledgeBase)
        if allowed is not None:
            q = q.filter(KnowledgeBase.id.in_(allowed))
        return [{"id": k.id, "name": k.name, "description": k.description}
                for k in q.order_by(KnowledgeBase.id)]

    @router.delete("/api/v1/kb/{kb_id}", status_code=204,
                   responses={**ERR(404, "知识库不存在"), **ERR_GATE})
    def delete_kb(kb_id: PathId, request: Request,
                  admin: User = Depends(require_license),
                  session: Session = Depends(get_session)):
        """删库：文档/切块/授权级联清，落盘原文件一并删。**用量台账保留**（kb_id 置 NULL）——
        成本账是事实来源，不能随库消失；API key 作用域与历史会话里的孤儿 kb_id 天然无害
        （召回走交集语义），不做事后清理。不可逆，前端要求输入库名二次确认。
        """
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        name = kb.name
        files = [d.storage_path for d in session.query(Document).filter_by(kb_id=kb_id)]
        doc_count = len(files)
        session.delete(kb)      # documents/chunks/user_kb_grants 走 FK CASCADE
        audit_record(session, "kb_deleted", user_email=admin.email,
                     target_type="kb", target_id=kb_id,
                     detail={"name": name, "documents": doc_count}, ip=client_ip(request))
        session.commit()
        for f in files:
            if not f:
                continue
            try:
                Path(f).unlink(missing_ok=True)
            except OSError as exc:   # 文件删不掉不该让接口失败：DB 已一致，残留文件无害
                import logging

                logging.getLogger("umax").warning("原文件清理失败 %s：%s", f, exc)

    @router.get("/api/v1/kb/{kb_id}/documents", response_model=list[DocOut],
                responses={**ERR(404, "知识库不存在"), **ERR_LOGIN_GATE})
    def list_documents(kb_id: PathId, user: User = Depends(get_user),
                       session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        # 不在授权范围内的库与"不存在"同文案：探测不出别人的库 id 存不存在
        if not session.get(KnowledgeBase, kb_id) or (allowed is not None and kb_id not in allowed):
            raise HTTPException(404, "知识库不存在")
        return [doc_json(d) for d in session.query(Document)
                .filter_by(kb_id=kb_id).order_by(Document.id)]

    return router
