# FastAPI 服务层：知识库/文档入库/检索/带引用问答/会话历史
# 一期无鉴权（登录与初始化向导在后续任务）；embedder/chat_fn 依赖注入，测试用假实现
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, BeforeValidator, Field, StrictBool
from sqlalchemy import func, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.models import (Chunk, Conversation, Document, KnowledgeBase, Message,
                        ModelConfig, UsageRecord)
from app.services.citations import parse_citations
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.ingest import ingest_document, read_stored, supported_ext
from app.services.retrieval import retrieve


SCENARIOS = {"chat", "embedding", "rerank", "vision"}
# 契约 fuzz 修复①：id 落 PG INTEGER（int32）——裸 integer 无界，fuzz 发 2^31 直接 SQL 溢出 500，
# 把 int32 边界写进契约。修复②：body 侧 Strict* 禁 bool/str 混入（lax 强转被 fuzz 判"违法请求被接受"）
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


def _json_int(v):
    # 按 JSON Schema 的 integer 语义对齐契约（fuzz 修复⑥）：整值浮点（-74.0）合法必须收，
    # bool/str/None/dict 等非法要拒——pydantic strict 过紧（拒 -74.0），lax 过松（收 True/"5"）
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("不是 JSON integer")
    if isinstance(v, float):
        if not v.is_integer():
            raise ValueError("不是 JSON integer")
        return int(v)
    return v


def _json_int32(v):
    # fuzz 修复⑦：Field(ge/le) 叠在 BeforeValidator 外层时 pydantic 吐非法 JSON Schema 键
    # "ge"/"le"（裸 Field 的 PathId 则正确吐 minimum/maximum）——契约校验器忽略未知键，
    # 越界值被判合法请求，运行时 422 即"拒绝合法请求"违约。改由验证器统一卡 int32 边界，
    # schema 用 json_schema_extra 显式写标准 minimum/maximum，两侧严格一致。
    v = _json_int(v)
    if not (INT32_MIN <= v <= INT32_MAX):
        raise ValueError("超出 int32（PG INTEGER）范围")
    return v


JsonInt = Annotated[int, BeforeValidator(_json_int)]
PathId = Annotated[int, Field(ge=INT32_MIN, le=INT32_MAX)]     # 路径 id：字符串→int 保持 lax，只卡 int32
ReqId = Annotated[int, BeforeValidator(_json_int32),           # body id：JSON integer 且卡 int32，
            Field(json_schema_extra={"minimum": INT32_MIN, "maximum": INT32_MAX})]  # schema 侧标准键


def _clean_text(v):
    # fuzz 修复⑧：契约里这些字段是 type:string，含 NUL(\u0000)/JSON 转义 \udXXX 孤立代理码点的
    # 字符串是合法正数据，但 PG 文本列存不了（NUL 直接 DataError、孤立代理无法编 UTF-8 →
    # UntranslatableCharacter，都是 500 违约）；而拒收 422 又犯"拒绝合法请求"违约，且 JSONB
    # 嵌套串（capabilities 值）在 schema 里根本无法表达排除约束。唯一两侧自洽的行为=规范化入库：
    # NUL 剔除、孤立代理→U+FFFD，dict/list 递归下钻（键与值都处理）。
    if isinstance(v, str):
        if "\x00" in v:
            v = v.replace("\x00", "")
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            v = "".join("\ufffd" if "\ud800" <= c <= "\udfff" else c for c in v)
        return v
    if isinstance(v, dict):
        return {_clean_text(k): _clean_text(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_text(x) for x in v]
    return v


def _utf8_str(v):
    return _clean_text(v)


Utf8Str = Annotated[str, BeforeValidator(_utf8_str)]           # 落库字符串统一过 NUL/代理规范化闸
JsonSafe = Annotated[dict, BeforeValidator(_utf8_str)]         # JSONB 列（capabilities）递归过闸
SCENARIO_PATTERN = "^(chat|embedding|rerank|vision)$"


class KbIn(BaseModel):
    name: Utf8Str
    description: Utf8Str | None = None


class RetrieveIn(BaseModel):
    query: Utf8Str
    kb_ids: list[ReqId] | None = None
    top_k: JsonInt | None = None


class ChatIn(BaseModel):
    question: Utf8Str
    kb_ids: list[ReqId] | None = None
    conversation_id: ReqId | None = None


class DocPatchIn(BaseModel):
    status: Utf8Str | None = None
    error: Utf8Str | None = None


class ModelIn(BaseModel):
    # scenario 枚举约束写进 schema（pattern）：运行时仍走 400 业务校验，但契约生成器不再
    # 拿任意合法字符串撞出 400 被判"合法请求被拒"（fuzz 修复③：约束没进 spec 才是根因）
    scenario: str = Field(json_schema_extra={"pattern": SCENARIO_PATTERN})
    provider: Utf8Str
    base_url: Utf8Str
    api_key: Utf8Str
    model_name: Utf8Str
    capabilities: JsonSafe = {}
    is_default: StrictBool = False
    fallback_rank: ReqId = 0
    enabled: StrictBool = True


class ModelPatchIn(BaseModel):
    scenario: Annotated[str, Field(json_schema_extra={"pattern": SCENARIO_PATTERN})] | None = None
    provider: Utf8Str | None = None
    base_url: Utf8Str | None = None
    api_key: Utf8Str | None = None
    model_name: Utf8Str | None = None
    capabilities: JsonSafe | None = None
    is_default: StrictBool | None = None
    fallback_rank: ReqId | None = None
    enabled: StrictBool | None = None


# 契约 fuzz 前提：真实错误码必须写进 spec，否则 schemathesis 判合法响应为违约
class ErrorOut(BaseModel):
    detail: str


_ERR = lambda code, msg: {code: {"model": ErrorOut, "description": msg}}  # noqa: E731
# fuzz 修复④：请求体不是合法 JSON 时 Starlette 直接回 400（FastAPI 默认 spec 只带 422）——按实声明
_ERR_BODY = _ERR(400, "请求体解析失败")

MISS_ANSWER = "资料里没有相关内容，无法回答。"


class _AllowHeaderMiddleware:
    """fuzz 修复⑤：同一路径由多个单方法路由拼成，Starlette 的 405/OPTIONS 只报首个路由的方法
    （AllowHeaderMismatch 抓到 Allow: POST 少了 GET，违反 RFC 9110）——合并为路径真实方法全集。"""

    def __init__(self, app: ASGIApp, routes) -> None:
        self.app, self.routes = app, routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        methods: set[str] = set()
        if scope["type"] == "http":
            for route in self.routes:
                allowed = getattr(route, "methods", None)
                if allowed:
                    matched = route.matches(scope)
                    match = matched[0] if isinstance(matched, tuple) else matched
                    if match in (Match.FULL, Match.PARTIAL):
                        methods |= allowed
        if not methods:
            await self.app(scope, receive, send)
            return

        async def send_with_allow(message: dict) -> None:
            if message["type"] == "http.response.start" and (
                    message["status"] == 405 or scope["method"] == "OPTIONS"):
                headers = MutableHeaders(raw=message["headers"])
                headers["allow"] = ", ".join(sorted(methods))
            await send(message)

        await self.app(scope, receive, send_with_allow)


def _doc_json(d: Document) -> dict:
    return {"id": d.id, "kb_id": d.kb_id, "name": d.name, "status": d.status,
            "error": d.error, "size_bytes": d.size_bytes}


def create_app(
    *,
    engine: Engine,
    embedder=None,
    chat_fn: Callable[[str, list[dict]], dict] | None = None,
    upload_dir: str = "uploads",
    queue=None,          # ImportQueue 协议；None=同步入库
    mineru=None,         # MinerUClient；None=扫描件解析直接失败并说明原因
    secret: str | None = None,  # 网关主密钥（GATEWAY_SECRET），None=读配置
) -> FastAPI:
    app = FastAPI(title="Umax RAG", version="0.1.0")
    s = get_settings()
    gateway_secret = s.gateway_secret if secret is None else secret

    def get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    @app.get("/api/v1/health")
    def health(session: Session = Depends(get_session)):
        session.execute(sa_text("SELECT 1"))
        return {"status": "ok"}

    # ---- 知识库 ----
    @app.post("/api/v1/kb", status_code=201, responses=_ERR_BODY)
    def create_kb(body: KbIn, session: Session = Depends(get_session)):
        kb = KnowledgeBase(tenant_id="default", name=body.name, description=body.description,
                           embedding_model=s.embedding_model, chunk_target=s.chunk_target)
        session.add(kb)
        session.commit()
        return {"id": kb.id, "name": kb.name, "description": kb.description}

    @app.get("/api/v1/kb")
    def list_kb(session: Session = Depends(get_session)):
        return [{"id": k.id, "name": k.name, "description": k.description}
                for k in session.query(KnowledgeBase).order_by(KnowledgeBase.id)]

    # ---- 文档与入库 ----
    @app.post("/api/v1/kb/{kb_id}/documents", status_code=201,
              responses={**_ERR(404, "知识库不存在"), **_ERR(415, "不支持的文件类型"),
                         **_ERR_BODY})
    def upload_document(kb_id: PathId, file: UploadFile = File(...),
                        session: Session = Depends(get_session)):
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        # 修复⑧收口：multipart filename 不经 pydantic 验证链，是唯一的裸入口字符串——
        # 取 basename（防目录注入）+ 过 NUL/孤立代理清洗闸 + 截 200（_doc_json 会回显给前端），
        # 否则 \x00 直接进 Path 拼接/write_bytes/PG 即未声明 500
        name = _clean_text(Path(file.filename or "unnamed").name)[:200]
        if not supported_ext(name):
            raise HTTPException(415, f"暂不支持的文件类型：{name}（一期 .txt/.md，MinerU 接入后支持 PDF/Office）")
        raw = file.file.read()
        doc = Document(tenant_id="default", kb_id=kb_id, name=name, status="pending",
                       size_bytes=len(raw), mime=file.content_type)
        session.add(doc)
        session.flush()
        p = Path(upload_dir) / f"{uuid4().hex[:8]}_{name}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        doc.storage_path = str(p)
        session.commit()
        if queue is not None:
            queue.enqueue_import(doc.id)
            return _doc_json(doc)  # pending，worker 接手
        doc = ingest_document(session, doc, raw, embedder=embedder, mineru=mineru,
                              chunk_target=s.chunk_target, chunk_min=s.chunk_min)
        return _doc_json(doc)

    @app.get("/api/v1/documents/{doc_id}",
             responses=_ERR(404, "文档不存在"))
    def get_document(doc_id: PathId, session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        return _doc_json(doc)

    @app.get("/api/v1/documents/{doc_id}/chunks",
             responses=_ERR(404, "文档不存在"))
    def preview_chunks(doc_id: PathId, session: Session = Depends(get_session)):
        if not session.get(Document, doc_id):
            raise HTTPException(404, "文档不存在")
        return [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content,
                 "has_embedding": c.embedding is not None}
                for c in session.query(Chunk).filter_by(document_id=doc_id)
                .order_by(Chunk.chunk_index)]

    @app.patch("/api/v1/documents/{doc_id}",
               responses={**_ERR(404, "文档不存在"), **_ERR_BODY})
    def patch_document(doc_id: PathId, body: DocPatchIn, session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        if body.status:
            doc.status = body.status
        doc.error = body.error
        session.commit()
        return _doc_json(doc)

    @app.post("/api/v1/documents/{doc_id}/reprocess", status_code=202,
              responses=_ERR(404, "文档不存在"))
    def reprocess(doc_id: PathId, session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        if queue is not None:
            doc.status, doc.error = "pending", None
            session.commit()
            queue.enqueue_import(doc.id)
            return _doc_json(doc)
        doc = ingest_document(session, doc, read_stored(doc), embedder=embedder,
                              mineru=mineru, chunk_target=s.chunk_target,
                              chunk_min=s.chunk_min)
        return _doc_json(doc)

    # ---- 检索与问答 ----
    @app.post("/api/v1/retrieve", responses=_ERR_BODY)
    def retrieve_api(body: RetrieveIn, session: Session = Depends(get_session)):
        return retrieve(session, body.query, embedder=embedder, kb_ids=body.kb_ids,
                        recall_k=s.recall_k, top_k=body.top_k or s.rerank_top_n,
                        min_sim=s.min_sim)

    @app.post("/api/v1/chat", responses=_ERR_BODY)
    def chat(body: ChatIn, session: Session = Depends(get_session)):
        hits = retrieve(session, body.question, embedder=embedder, kb_ids=body.kb_ids,
                        recall_k=s.recall_k, top_k=s.rerank_top_n, min_sim=s.min_sim)
        conv = session.get(Conversation, body.conversation_id) if body.conversation_id else None
        if conv is None:
            conv = Conversation(tenant_id="default", user_email="default@local",
                                title=body.question[:32], kb_ids=body.kb_ids or [])
            session.add(conv)
            session.flush()
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="user",
                            content=[{"type": "text", "text": body.question}]))

        if not hits or chat_fn is None:
            answer, usage, citations = MISS_ANSWER, {"prompt_tokens": 0, "completion_tokens": 0}, []
        else:
            out = chat_fn(body.question, hits)
            answer = out["answer"]
            usage = {"prompt_tokens": out.get("prompt_tokens", 0),
                     "completion_tokens": out.get("completion_tokens", 0)}
            citations = [{"n": i, "doc_name": h["doc_name"], "chunk_id": h["id"],
                          "excerpt": h["content"][:80]} for i, h in enumerate(hits, 1)]
            if not out.get("logged"):  # 网关 make_chat_fn 已自行记账，避免双记
                session.add(UsageRecord(tenant_id="default", user_email="default@local",
                                        scenario="chat", model=out.get("model") or s.chat_model,
                                        prompt_tokens=usage["prompt_tokens"],
                                        completion_tokens=usage["completion_tokens"],
                                        latency_ms=out.get("latency_ms")))
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="assistant",
                            content=[{"type": "text", "text": answer}], citations=citations))
        session.commit()
        return {"conversation_id": conv.id, "answer": answer, "citations": citations,
                "cited_docs": parse_citations(answer, hits), "usage": usage}

    # ---- 会话历史 ----
    @app.get("/api/v1/conversations")
    def list_conversations(session: Session = Depends(get_session)):
        return [{"id": c.id, "title": c.title, "kb_ids": c.kb_ids}
                for c in session.query(Conversation).order_by(Conversation.id)]

    @app.get("/api/v1/conversations/{conv_id}/messages",
             responses=_ERR(404, "会话不存在"))
    def list_messages(conv_id: PathId, session: Session = Depends(get_session)):
        if not session.get(Conversation, conv_id):
            raise HTTPException(404, "会话不存在")
        return [{"id": m.id, "role": m.role, "content": m.content, "citations": m.citations}
                for m in session.query(Message).filter_by(conversation_id=conv_id)
                .order_by(Message.id)]

    # ---- 模型后台（§C：改表即生效；key 加密存储、打码、不回传明文）----
    def _require_secret() -> str:
        if not gateway_secret:
            raise HTTPException(503, "未配置主密钥 GATEWAY_SECRET，无法管理模型 key")
        return gateway_secret

    def _model_json(m: ModelConfig) -> dict:
        plain = decrypt_secret(m.encrypted_api_key, _require_secret())
        return {"id": m.id, "scenario": m.scenario, "provider": m.provider,
                "base_url": m.base_url, "model_name": m.model_name,
                "capabilities": m.capabilities, "is_default": m.is_default,
                "fallback_rank": m.fallback_rank, "enabled": m.enabled,
                "api_key_masked": ("****" + plain[-4:]) if plain else ""}

    @app.post("/api/v1/models", status_code=201,
              responses={**_ERR(400, "scenario 非法"), **_ERR(503, "未配置 GATEWAY_SECRET")})
    def create_model(body: ModelIn, session: Session = Depends(get_session)):
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, f"scenario 仅支持 {'/'.join(sorted(SCENARIOS))}")
        m = ModelConfig(tenant_id="default", scenario=body.scenario, provider=body.provider,
                        base_url=body.base_url, model_name=body.model_name,
                        encrypted_api_key=encrypt_secret(body.api_key, _require_secret()),
                        capabilities=body.capabilities, is_default=body.is_default,
                        fallback_rank=body.fallback_rank, enabled=body.enabled)
        session.add(m)
        session.commit()
        return _model_json(m)

    @app.get("/api/v1/models")
    def list_models(session: Session = Depends(get_session)):
        rows = session.query(ModelConfig).order_by(ModelConfig.scenario,
                                                   ModelConfig.fallback_rank, ModelConfig.id)
        return [_model_json(m) for m in rows]

    @app.patch("/api/v1/models/{model_id}",
               responses={**_ERR(400, "scenario 非法"), **_ERR(404, "模型配置不存在"),
                          **_ERR(503, "未配置 GATEWAY_SECRET")})
    def patch_model(model_id: PathId, body: ModelPatchIn, session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if not m:
            raise HTTPException(404, "模型配置不存在")
        data = body.model_dump(exclude_none=True)
        if "scenario" in data and data["scenario"] not in SCENARIOS:
            raise HTTPException(400, f"scenario 仅支持 {'/'.join(sorted(SCENARIOS))}")
        if "api_key" in data:
            data["encrypted_api_key"] = encrypt_secret(data.pop("api_key"), _require_secret())
        for k, v in data.items():
            setattr(m, k, v)
        session.commit()
        return _model_json(m)

    @app.delete("/api/v1/models/{model_id}", status_code=204,
                responses=_ERR(503, "未配置 GATEWAY_SECRET"))
    def delete_model(model_id: PathId, session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if m:
            session.delete(m)
            session.commit()

    # ---- 用量看板简版（§C：成本折算的事实来源）----
    @app.get("/api/v1/usage/summary")
    def usage_summary(session: Session = Depends(get_session)):
        rows = (session.query(UsageRecord.scenario, UsageRecord.model,
                              func.count().label("calls"),
                              func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
                              func.coalesce(func.sum(UsageRecord.completion_tokens), 0))
                .group_by(UsageRecord.scenario, UsageRecord.model).all())
        return [{"scenario": r[0], "model": r[1], "calls": r[2],
                 "prompt_tokens": int(r[3]), "completion_tokens": int(r[4])} for r in rows]

    app.add_middleware(_AllowHeaderMiddleware, routes=app.router.routes)
    return app


def build_production_app(upload_dir: str = "uploads",
                         engine: Engine | None = None) -> FastAPI:
    """真依赖装配：配了 GATEWAY_SECRET 且表里有对应场景模型 → 走网关（后台改表即生效）；
    否则退回 .env 里的百炼直连（阶段 0 链路，冒烟可跑）。"""
    from sqlalchemy import create_engine

    from app.db.base import Base
    from app.services.chat import ChatClient, make_chat_fn
    from app.services.embeddings import BailianEmbedder
    from app.services.parsers import MinerUClient

    s = get_settings()
    if engine is None:
        engine = create_engine(s.sqlalchemy_url(), pool_pre_ping=True)
        import app.models  # noqa: F401  一键部署：启动即建表（正式迁移方案后续以 Alembic 接管）
        Base.metadata.create_all(engine)
    chat_fn = embedder = None
    if s.gateway_secret:
        from app.services.gateway import ModelGateway

        gw = ModelGateway(engine, secret=s.gateway_secret)
        if gw.providers("chat"):
            chat_fn = gw.make_chat_fn()
        if gw.providers("embedding"):
            embedder = gw.make_embedder()
    if (chat_fn is None or embedder is None) and s.dashscope_api_key:
        chat_fn = chat_fn or make_chat_fn(ChatClient(api_key=s.dashscope_api_key,
                                                     base_url=s.dashscope_compat_base,
                                                     model=s.chat_model))
        embedder = embedder or BailianEmbedder(api_key=s.dashscope_api_key,
                                               base_url=s.dashscope_compat_base,
                                               model=s.embedding_model,
                                               dimensions=s.embedding_dim)
    mineru = MinerUClient(s.mineru_base_url) if s.mineru_base_url else None
    queue = None
    if s.queue_backend == "arq":
        from app.queue import ArqQueue

        queue = ArqQueue(redis_host=s.redis_host, redis_port=s.redis_port)
    return create_app(engine=engine, embedder=embedder, chat_fn=chat_fn,
                      upload_dir=upload_dir, mineru=mineru, queue=queue)


def main() -> None:
    import uvicorn

    uvicorn.run(build_production_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
