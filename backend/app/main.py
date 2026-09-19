# FastAPI 服务层：知识库/文档入库/检索/带引用问答/会话历史
# 一期无鉴权（登录与初始化向导在后续任务）；embedder/chat_fn 依赖注入，测试用假实现
from collections.abc import Callable, Iterator
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (Chunk, Conversation, Document, KnowledgeBase, Message,
                        ModelConfig, UsageRecord)
from app.services.citations import parse_citations
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.ingest import ingest_document, read_stored, supported_ext
from app.services.retrieval import retrieve


class KbIn(BaseModel):
    name: str
    description: str | None = None


class RetrieveIn(BaseModel):
    query: str
    kb_ids: list[int] | None = None
    top_k: int | None = None


class ChatIn(BaseModel):
    question: str
    kb_ids: list[int] | None = None
    conversation_id: int | None = None


class DocPatchIn(BaseModel):
    status: str | None = None
    error: str | None = None


class ModelIn(BaseModel):
    scenario: str
    provider: str
    base_url: str
    api_key: str
    model_name: str
    capabilities: dict = {}
    is_default: bool = False
    fallback_rank: int = 0
    enabled: bool = True


class ModelPatchIn(BaseModel):
    scenario: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    capabilities: dict | None = None
    is_default: bool | None = None
    fallback_rank: int | None = None
    enabled: bool | None = None


SCENARIOS = {"chat", "embedding", "rerank", "vision"}

MISS_ANSWER = "资料里没有相关内容，无法回答。"


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

    @app.get("/api/health")
    def health(session: Session = Depends(get_session)):
        session.execute(sa_text("SELECT 1"))
        return {"status": "ok"}

    # ---- 知识库 ----
    @app.post("/api/kb", status_code=201)
    def create_kb(body: KbIn, session: Session = Depends(get_session)):
        kb = KnowledgeBase(tenant_id="default", name=body.name, description=body.description,
                           embedding_model=s.embedding_model, chunk_target=s.chunk_target)
        session.add(kb)
        session.commit()
        return {"id": kb.id, "name": kb.name, "description": kb.description}

    @app.get("/api/kb")
    def list_kb(session: Session = Depends(get_session)):
        return [{"id": k.id, "name": k.name, "description": k.description}
                for k in session.query(KnowledgeBase).order_by(KnowledgeBase.id)]

    # ---- 文档与入库 ----
    @app.post("/api/kb/{kb_id}/documents", status_code=201)
    def upload_document(kb_id: int, file: UploadFile = File(...),
                        session: Session = Depends(get_session)):
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        name = file.filename or "unnamed"
        if not supported_ext(name):
            raise HTTPException(415, f"暂不支持的文件类型：{name}（一期 .txt/.md，MinerU 接入后支持 PDF/Office）")
        raw = file.file.read()
        doc = Document(tenant_id="default", kb_id=kb_id, name=name, status="pending",
                       size_bytes=len(raw), mime=file.content_type)
        session.add(doc)
        session.flush()
        import pathlib
        p = pathlib.Path(upload_dir) / f"{uuid4().hex[:8]}_{name}"
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

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: int, session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        return _doc_json(doc)

    @app.get("/api/documents/{doc_id}/chunks")
    def preview_chunks(doc_id: int, session: Session = Depends(get_session)):
        if not session.get(Document, doc_id):
            raise HTTPException(404, "文档不存在")
        return [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content,
                 "has_embedding": c.embedding is not None}
                for c in session.query(Chunk).filter_by(document_id=doc_id)
                .order_by(Chunk.chunk_index)]

    @app.patch("/api/documents/{doc_id}")
    def patch_document(doc_id: int, body: DocPatchIn, session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        if body.status:
            doc.status = body.status
        doc.error = body.error
        session.commit()
        return _doc_json(doc)

    @app.post("/api/documents/{doc_id}/reprocess", status_code=202)
    def reprocess(doc_id: int, session: Session = Depends(get_session)):
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
    @app.post("/api/retrieve")
    def retrieve_api(body: RetrieveIn, session: Session = Depends(get_session)):
        return retrieve(session, body.query, embedder=embedder, kb_ids=body.kb_ids,
                        recall_k=s.recall_k, top_k=body.top_k or s.rerank_top_n,
                        min_sim=s.min_sim)

    @app.post("/api/chat")
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
    @app.get("/api/conversations")
    def list_conversations(session: Session = Depends(get_session)):
        return [{"id": c.id, "title": c.title, "kb_ids": c.kb_ids}
                for c in session.query(Conversation).order_by(Conversation.id)]

    @app.get("/api/conversations/{conv_id}/messages")
    def list_messages(conv_id: int, session: Session = Depends(get_session)):
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

    @app.post("/api/models", status_code=201)
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

    @app.get("/api/models")
    def list_models(session: Session = Depends(get_session)):
        rows = session.query(ModelConfig).order_by(ModelConfig.scenario,
                                                   ModelConfig.fallback_rank, ModelConfig.id)
        return [_model_json(m) for m in rows]

    @app.patch("/api/models/{model_id}")
    def patch_model(model_id: int, body: ModelPatchIn, session: Session = Depends(get_session)):
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

    @app.delete("/api/models/{model_id}", status_code=204)
    def delete_model(model_id: int, session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if m:
            session.delete(m)
            session.commit()

    # ---- 用量看板简版（§C：成本折算的事实来源）----
    @app.get("/api/usage/summary")
    def usage_summary(session: Session = Depends(get_session)):
        rows = (session.query(UsageRecord.scenario, UsageRecord.model,
                              func.count().label("calls"),
                              func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
                              func.coalesce(func.sum(UsageRecord.completion_tokens), 0))
                .group_by(UsageRecord.scenario, UsageRecord.model).all())
        return [{"scenario": r[0], "model": r[1], "calls": r[2],
                 "prompt_tokens": int(r[3]), "completion_tokens": int(r[4])} for r in rows]

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
