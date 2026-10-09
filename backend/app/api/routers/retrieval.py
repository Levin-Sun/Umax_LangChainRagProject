# 检索与问答 + 会话历史。
#
# 可见性在检索层钳制（SQL 谓词），命中集就是授权集的子集——所以这里看到的是"过滤后的检索"，
# 而不是"检索后过滤"。会话历史按登录者隔离（admin 也没有特权看别人的会话）。
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.constants import IMAGE_COUNT_MAX, IMAGE_MAX, LOGO_RE
from app.api.deps import Runtime, allowed_kb_ids, get_session, get_user, guard_kb_ids
from app.api.schemas import (ERR, ERR_BODY, ERR_GATE, ERR_LOGIN_GATE, ChatIn, ChatOut, ConversationOut,
                             HitOut, MessageOut, PathId, RetrieveIn)
from app.models import Conversation, Message, UsageRecord, User
from app.services.citations import parse_citations
from app.services.retrieval import retrieve
from app.services.text import clean_text


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/retrieve", response_model=list[HitOut], responses={**ERR_BODY, **ERR_GATE})
    def retrieve_api(body: RetrieveIn, user: User = Depends(get_user),
                     session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        guard_kb_ids(allowed, body.kb_ids)
        c = rt.cfg.effective()
        return retrieve(session, body.query, embedder=rt.embedder, kb_ids=body.kb_ids,
                        allowed_kb_ids=allowed, rerank=rt.rerank,
                        recall_k=c["recall_k"], top_k=body.top_k or c["rerank_top_n"],
                        min_sim=c["min_sim"])

    @router.post("/api/v1/chat", response_model=ChatOut,
                 responses={**ERR(404, "会话不存在"), **ERR(429, "配额已用尽"), **ERR_BODY, **ERR_GATE})
    def chat(body: ChatIn, user: User = Depends(get_user),
             session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        guard_kb_ids(allowed, body.kb_ids)
        c = rt.cfg.effective()   # 每请求现读配置：后台改检索参数/提示词即时生效
        rt.enforce_quota(session, user)   # 配额闸门：超限在检索/调模型之前拦下
        # 传图提问（阶段 2）：先校验图片，再由 vision 模型转文字描述，拼进检索与生成的问题
        images = body.images or []
        if len(images) > IMAGE_COUNT_MAX:
            raise HTTPException(400, f"最多附带 {IMAGE_COUNT_MAX} 张图片")
        for u in images:
            if not LOGO_RE.match(u):
                raise HTTPException(400, "图片仅支持 data:image/* base64")
            if len(u) > IMAGE_MAX:
                raise HTTPException(400, "单张图片过大（≤2MB）")
        captions: list[str] = []
        for u in images:
            if rt.vision_fn is None:
                break   # 未配视觉模型：文字照常答（图片仍随消息存储可回看）
            try:
                out = rt.vision_fn(u)
            except Exception as exc:
                import logging
                logging.getLogger("umax").warning("vision 描述失败，跳过该图：%s", exc)
                continue
            captions.append(out["caption"])
            session.add(UsageRecord(tenant_id="default", user_email=user.email,
                                    scenario="vision", model=out.get("model") or rt.settings.chat_model,
                                    prompt_tokens=out.get("prompt_tokens") or 0,
                                    completion_tokens=out.get("completion_tokens") or 0,
                                    latency_ms=out.get("latency_ms")))
        query = body.question
        if captions:
            query += "\n\n【随问图片描述】\n" + "\n".join(captions)
        hits = retrieve(session, query, embedder=rt.embedder, kb_ids=body.kb_ids,
                        allowed_kb_ids=allowed, rerank=rt.rerank,
                        recall_k=c["recall_k"], top_k=c["rerank_top_n"], min_sim=c["min_sim"])
        # 终审收口①：给了 id 就必须"存在且归调用者"——不存在与跨用户同文案（同 list_messages），
        # 否则"不存在→新建回 200 / 别人的→404"成了会话存在性探测口（spec §0：不可区分）。
        # 只有 conversation_id 缺席（null）才新建会话。
        conv = session.get(Conversation, body.conversation_id) if body.conversation_id is not None else None
        if body.conversation_id is not None and (conv is None or conv.user_email != user.email):
            raise HTTPException(404, "会话不存在")
        if conv is None:
            conv = Conversation(tenant_id="default", user_email=user.email,
                                title=body.question[:32], kb_ids=body.kb_ids or [])
            session.add(conv)
            session.flush()
        user_parts: list[dict] = [{"type": "text", "text": body.question}]
        user_parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="user",
                            content=user_parts))

        degraded: str | None = None
        if not hits or rt.chat_fn is None:
            # 兜底也要说清"为什么兜底"：检索真的空 vs 压根没有模型可用（见 ChatOut.degraded_reason）
            degraded = "no_hit" if not hits else "no_model"
            answer, usage, citations = c["chat_miss_answer"], {"prompt_tokens": 0, "completion_tokens": 0}, []
        else:
            try:
                out = rt.chat_fn(query, hits)
            # 模型在两次问答之间被停用/删光/全挂：不把 500 甩用户脸上，转未命中兜底
            except Exception as exc:
                import logging
                logging.getLogger("umax").warning("chat 生成失败，转未命中兜底：%s", exc)
                out = None
            if out is None:
                degraded = "model_error"
                answer, usage, citations = c["chat_miss_answer"], {"prompt_tokens": 0, "completion_tokens": 0}, []
            else:
                answer = clean_text(out["answer"])   # 模型输出也过规范化闸（否则 NUL 直接 500）
                usage = {"prompt_tokens": out.get("prompt_tokens") or 0,
                         "completion_tokens": out.get("completion_tokens") or 0}
                citations = [{"n": i, "doc_name": h["doc_name"], "chunk_id": h["id"],
                              "excerpt": h["content"][:80]} for i, h in enumerate(hits, 1)]
                # 记账单点在端点：网关 make_chat_fn 已交回记账职责（logged 约定退役），
                # 这里必记且只记一次，台账归真实登录者而非占位邮箱
                session.add(UsageRecord(tenant_id="default", user_email=user.email,
                                        scenario="chat", model=out.get("model") or rt.settings.chat_model,
                                        prompt_tokens=usage["prompt_tokens"],
                                        completion_tokens=usage["completion_tokens"],
                                        latency_ms=out.get("latency_ms")))
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="assistant",
                            content=[{"type": "text", "text": answer}], citations=citations))
        session.commit()
        return {"conversation_id": conv.id, "answer": answer, "citations": citations,
                "cited_docs": parse_citations(answer, hits), "usage": usage,
                "degraded_reason": degraded}

    # ---- 会话历史：按登录者隔离（admin 也没有特权看别人的会话）----
    @router.get("/api/v1/conversations", response_model=list[ConversationOut], responses={**ERR_LOGIN_GATE})
    def list_conversations(q: Annotated[str | None, Query(max_length=64)] = None,
                           user: User = Depends(get_user),
                           session: Session = Depends(get_session)):
        # 会话搜索（backlog）：标题子串匹配，仅本人会话；q 里的 LIKE 通配符按字面剔除
        query = session.query(Conversation).filter(Conversation.user_email == user.email)
        if q:
            literal = q.replace("%", "").replace("_", "").replace("\\", "")
            if not literal:
                return []   # 纯通配符按字面语义＝无标题含这些字符
            query = query.filter(Conversation.title.ilike(f"%{literal}%"))
        return [{"id": c.id, "title": c.title, "kb_ids": c.kb_ids}
                for c in query.order_by(Conversation.id)]

    @router.delete("/api/v1/conversations/{conv_id}", status_code=204,
                   responses={**ERR(404, "会话不存在"), **ERR_LOGIN_GATE})
    def delete_conversation(conv_id: PathId, user: User = Depends(get_user),
                            session: Session = Depends(get_session)):
        conv = session.get(Conversation, conv_id)
        if not conv or conv.user_email != user.email:
            raise HTTPException(404, "会话不存在")
        session.delete(conv)   # messages 走 FK CASCADE；自助数据管理不进审计流
        session.commit()

    @router.get("/api/v1/conversations/{conv_id}/messages", response_model=list[MessageOut],
                responses={**ERR(404, "会话不存在"), **ERR_LOGIN_GATE})
    def list_messages(conv_id: PathId, user: User = Depends(get_user),
                      session: Session = Depends(get_session)):
        conv = session.get(Conversation, conv_id)
        if not conv or conv.user_email != user.email:
            raise HTTPException(404, "会话不存在")
        return [{"id": m.id, "role": m.role, "content": m.content, "citations": m.citations}
                for m in session.query(Message).filter_by(conversation_id=conv_id)
                .order_by(Message.id)]

    return router
