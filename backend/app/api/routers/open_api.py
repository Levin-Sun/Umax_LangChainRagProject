# 开放 API（§2.2）：API key 管理（admin 面）+ OpenAI 兼容端点（Bearer key）。
#
# 兼容端点不走会话（客户系统没有浏览器 cookie），401/429/400 声明在该端点自己名下；
# 契约守卫为它开 Bearer 例外面（见 test_contract_declared）。两条通路共用
# Runtime.bearer_key / month_used_tokens，配额口径不会一边改一边漏。
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import Runtime, client_ip, get_session, now, require_admin_role, require_license
from app.api.schemas import (ERR, ERR_BODY, ERR_GATE, ApiKeyCreatedOut, ApiKeyIn, ApiKeyOut,
                             ApiKeyPatchIn, OpenAiChatIn, OpenAiChatOut, PathId)
from app.api.serializers import api_key_json
from app.models import ApiKey, UsageRecord, User
from app.services.audit import record as audit_record
from app.services.auth import new_api_key
from app.services.retrieval import retrieve
from app.services.text import clean_text


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/api-keys", status_code=201, response_model=ApiKeyCreatedOut,
                 responses={**ERR(400, "存在不存在的知识库 id"), **ERR_GATE, **ERR_BODY})
    def create_api_key(body: ApiKeyIn, request: Request,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        ids = rt.valid_kb_ids(session, body.kb_ids)
        plain, digest = new_api_key()
        k = ApiKey(tenant_id="default", name=body.name, key_hash=digest, key_prefix=plain[:13],
                   kb_ids=ids, monthly_token_quota=body.monthly_token_quota, created_by=admin.id)
        session.add(k)
        session.flush()
        # key 明文/摘要一律不入审计
        audit_record(session, "api_key_created", user_email=admin.email,
                     target_type="api_key", target_id=k.id,
                     detail={"name": body.name, "kb_ids": ids,
                             "monthly_token_quota": body.monthly_token_quota},
                     ip=client_ip(request))
        session.commit()
        out = api_key_json(k)
        out["key"] = plain   # 明文只在这一次响应里出现
        return out

    @router.get("/api/v1/api-keys", response_model=list[ApiKeyOut], responses={**ERR_GATE})
    def list_api_keys(admin: User = Depends(require_admin_role),
                      session: Session = Depends(get_session)):
        return [api_key_json(k) for k in session.query(ApiKey).order_by(ApiKey.id)]

    @router.patch("/api/v1/api-keys/{key_id}", response_model=ApiKeyOut,
                  responses={**ERR(400, "存在不存在的知识库 id"), **ERR(404, "API key 不存在"),
                             **ERR_GATE, **ERR_BODY})
    def patch_api_key(key_id: PathId, body: ApiKeyPatchIn, request: Request,
                      admin: User = Depends(require_license),
                      session: Session = Depends(get_session)):
        k = session.get(ApiKey, key_id)
        if not k:
            raise HTTPException(404, "API key 不存在")
        fields: list[str] = []
        if body.name is not None and body.name != k.name:
            k.name = body.name
            fields.append("name")
        if body.kb_ids is not None:
            k.kb_ids = rt.valid_kb_ids(session, body.kb_ids)
            fields.append("kb_ids")
        if body.monthly_token_quota is not None:
            k.monthly_token_quota = body.monthly_token_quota
            fields.append("monthly_token_quota")
        if body.enabled is not None and body.enabled != k.enabled:
            k.enabled = body.enabled
            fields.append("enabled")
        if fields:
            audit_record(session, "api_key_updated", user_email=admin.email,
                         target_type="api_key", target_id=k.id,
                         detail={"fields": fields}, ip=client_ip(request))
            session.commit()
        return api_key_json(k)

    @router.delete("/api/v1/api-keys/{key_id}", status_code=204,
                   responses={**ERR(404, "API key 不存在"), **ERR_GATE})
    def delete_api_key(key_id: PathId, request: Request,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        k = session.get(ApiKey, key_id)
        if not k:
            raise HTTPException(404, "API key 不存在")
        name = k.name   # 删前取：行没了就查不到被删的是哪枚
        session.delete(k)
        audit_record(session, "api_key_deleted", user_email=admin.email,
                     target_type="api_key", target_id=key_id,
                     detail={"name": name}, ip=client_ip(request))
        session.commit()

    @router.post("/api/v1/openai/chat/completions", response_model=OpenAiChatOut,
                 responses={**ERR(400, "messages 里没有 user 消息"), **ERR(401, "无效的 API key"),
                            **ERR(429, "本月配额已用尽")})
    def openai_chat_completions(body: OpenAiChatIn, request: Request,
                                session: Session = Depends(get_session)):
        k = rt.bearer_key(request, session)
        if k.monthly_token_quota is not None and rt.month_used_tokens(session, k.id) >= k.monthly_token_quota:
            raise HTTPException(429, "本月配额已用尽")
        question = next((m.content for m in reversed(body.messages) if m.role == "user"), None)
        if not question:
            raise HTTPException(400, "messages 里没有 user 消息")
        # 作用域钳制在检索层（同 user_kb_grants 方向）：kb_ids=NULL 全库，数组=限定库
        allowed = set(k.kb_ids) if k.kb_ids is not None else None
        c = rt.cfg.effective()
        hits = retrieve(session, question, embedder=rt.embedder, kb_ids=None,
                        allowed_kb_ids=allowed, rerank=rt.rerank, recall_k=c["recall_k"],
                        top_k=c["rerank_top_n"], min_sim=c["min_sim"])
        if not hits or rt.chat_fn is None:
            answer, usage, citations = c["chat_miss_answer"], {"prompt_tokens": 0, "completion_tokens": 0}, []
        else:
            out = rt.chat_fn(question, hits)
            answer = clean_text(out["answer"])   # 模型输出也过规范化闸（否则 NUL 直接 500）
            usage = {"prompt_tokens": out.get("prompt_tokens") or 0,
                     "completion_tokens": out.get("completion_tokens") or 0}
            citations = [{"n": i, "doc_name": h["doc_name"], "chunk_id": h["id"],
                          "excerpt": h["content"][:80]} for i, h in enumerate(hits, 1)]
            # 台账"谁"维度：apikey:{id}（配额聚合同口径）；model 取请求声明，缺省回默认
            session.add(UsageRecord(tenant_id="default", user_email=f"apikey:{k.id}",
                                    scenario="chat", model=body.model or rt.settings.chat_model,
                                    prompt_tokens=usage["prompt_tokens"],
                                    completion_tokens=usage["completion_tokens"],
                                    latency_ms=out.get("latency_ms")))
        k.last_used_at = now()
        session.commit()
        return {"id": f"chatcmpl-{uuid4().hex[:12]}", "object": "chat.completion",
                "created": int(now().timestamp()), "model": body.model or rt.settings.chat_model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": usage["prompt_tokens"],
                          "completion_tokens": usage["completion_tokens"],
                          "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"]},
                "citations": citations}   # 本产品扩展字段：OpenAI 没有但企业客户要溯源

    return router
