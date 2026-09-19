# ARQ worker：慢活（解析→切块→向量化）排队执行，界面轮询 documents.status
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Document
from app.services.ingest import ingest_document


def run_import(*, document_id: int, engine=None, embedder=None, mineru=None) -> dict:
    """执行单个文档的导入流水线（worker 与同步模式共用）。"""
    s = get_settings()
    own_session = engine is None
    if own_session:
        engine = create_engine(s.sqlalchemy_url())
    with Session(engine) as session:
        doc = session.get(Document, document_id)
        if doc is None:
            return {"document_id": document_id, "status": "failed",
                    "error": f"文档 {document_id} 不存在"}
        raw = open(doc.storage_path, "rb").read()
        doc = ingest_document(session, doc, raw, embedder=embedder, mineru=mineru,
                              chunk_target=s.chunk_target, chunk_min=s.chunk_min)
        return {"document_id": doc.id, "status": doc.status, "error": doc.error}


# ---- ARQ 入口：python -m arq app.worker.WorkerSettings ----

async def _startup(ctx: dict) -> None:
    s = get_settings()
    from app.services.embeddings import BailianEmbedder
    from app.services.parsers import MinerUClient

    ctx["engine"] = create_engine(s.sqlalchemy_url(), pool_pre_ping=True)
    ctx["embedder"] = BailianEmbedder(api_key=s.dashscope_api_key,
                                      base_url=s.dashscope_compat_base,
                                      model=s.embedding_model,
                                      dimensions=s.embedding_dim)
    ctx["mineru"] = MinerUClient(s.mineru_base_url) if s.mineru_base_url else None


async def import_document(ctx: dict, document_id: int) -> dict:
    return run_import(document_id=document_id, engine=ctx["engine"],
                      embedder=ctx.get("embedder"), mineru=ctx.get("mineru"))


class WorkerSettings:
    from arq.connections import RedisSettings

    redis_settings = RedisSettings(host="localhost", port=6379)
    functions = [import_document]
    on_startup = _startup
    max_jobs = 4  # 解析吃内存，单机起步
