# 入库流水线：解析（注册表路由，扫描件→MinerU）→切块→向量化→写 chunks
# 调度：queue 参数为空时 API 同步调用；配 ARQ 后由 worker 的 run_import 调用
from collections.abc import Callable

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import Chunk, Document
from app.services.chunking import chunk_text
from app.services.parsers import parse_document, supported_extensions


def supported_ext(filename: str) -> bool:
    ext = filename[filename.rfind("."):].lower() if "." in filename else ""
    return ext in supported_extensions()


def ingest_document(
    session: Session,
    doc: Document,
    raw: bytes,
    *,
    embedder=None,
    mineru=None,
    chunk_target: int = 300,
    chunk_min: int = 60,
) -> Document:
    doc.status = "parsing"
    session.commit()
    try:
        text = parse_document(doc.name, raw, mineru=mineru)
        pieces = chunk_text(text, target=chunk_target, min_len=chunk_min)
        vectors = embedder.embed(pieces) if (embedder and pieces) else [None] * len(pieces)
        session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        session.add_all([
            Chunk(tenant_id=doc.tenant_id, document_id=doc.id, kb_id=doc.kb_id,
                  chunk_index=i, content=c, embedding=v)
            for i, (c, v) in enumerate(zip(pieces, vectors))
        ])
        doc.status = "ready"
        doc.error = None
    except Exception as e:
        doc.status = "failed"
        doc.error = f"{e.__class__.__name__}: {e}"
    session.commit()
    return doc


def read_stored(doc: Document) -> bytes:
    return open(doc.storage_path, "rb").read()
