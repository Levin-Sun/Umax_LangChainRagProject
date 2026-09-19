# 同步入库流水线：抽取文本→切块→向量化→写 chunks（任务 5 换 ARQ+MinerU 时替换抽取与调度）
from collections.abc import Callable, Sequence

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import Chunk, Document
from app.services.chunking import chunk_text

TEXT_EXTS = {".txt", ".md"}


def supported_ext(filename: str) -> bool:
    return filename[filename.rfind("."):].lower() in TEXT_EXTS if "." in filename else False


def extract_text(filename: str, raw: bytes) -> str:
    """一期仅纯文本类；PDF/Word/扫描件在任务 5 接 MinerU。"""
    return raw.decode("utf-8")


def ingest_document(
    session: Session,
    doc: Document,
    raw: bytes,
    *,
    embedder=None,
    chunk_target: int = 300,
    chunk_min: int = 60,
    read_file: Callable[[str], bytes] = lambda p: open(p, "rb").read(),
) -> Document:
    doc.status = "parsing"
    session.commit()
    try:
        text = extract_text(doc.name, raw)
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


def reingest(session: Session, doc: Document, *, embedder=None,
             chunk_target: int = 300, chunk_min: int = 60) -> Document:
    """从落盘原始文件重跑流水线（reprocess 端点与失败重试共用）。"""
    raw = open(doc.storage_path, "rb").read()
    return ingest_document(session, doc, raw, embedder=embedder,
                           chunk_target=chunk_target, chunk_min=chunk_min)
