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


def _caption_chunks(session: Session, doc: Document, raw: bytes, vision_fn) -> list[dict]:
    """把文档内嵌图片交给视觉模型转文字描述（§A 图表入库）。

    失败隔离：单张图的视觉调用失败只跳过该图——图注是增强，正文才是主体，
    不能让"视觉服务抖了一下"把整篇文档判失败。
    """
    from app.services.media import extract_images

    out: list[dict] = []
    for i, blob in enumerate(extract_images(doc.name, raw), 1):
        try:
            res = vision_fn(blob.data_url())
        except Exception as exc:
            import logging

            logging.getLogger("umax").warning("文档 %s 第 %d 张图注失败，跳过：%s",
                                              doc.name, i, exc)
            continue
        caption = (res.get("caption") or "").strip()
        if not caption:
            continue
        out.append({"content": f"【图片内容】{caption}", "embedding": None,
                    "meta": {"source": "image", "image_index": i, "image_origin": blob.origin,
                             "width": blob.width, "height": blob.height}})
        # 视觉调用记账（场景 vision）：入库路径无请求人上下文，沿用网关的 system 口径
        from app.models import UsageRecord

        session.add(UsageRecord(tenant_id=doc.tenant_id, user_email="system@local",
                                kb_id=doc.kb_id, scenario="vision",
                                model=res.get("model") or "vision",
                                prompt_tokens=res.get("prompt_tokens") or 0,
                                completion_tokens=res.get("completion_tokens") or 0,
                                latency_ms=res.get("latency_ms")))
    return out


def ingest_document(
    session: Session,
    doc: Document,
    raw: bytes,
    *,
    embedder=None,
    mineru=None,
    chunk_target: int = 300,
    chunk_min: int = 60,
    vision_fn=None,          # 视觉模型适配器；None=未配置视觉，跳过图注
    caption_images: bool = True,
) -> Document:
    doc.status = "parsing"
    session.commit()
    try:
        text = parse_document(doc.name, raw, mineru=mineru)
        pieces = chunk_text(text, target=chunk_target, min_len=chunk_min)
        image_chunks = (_caption_chunks(session, doc, raw, vision_fn)
                        if (vision_fn is not None and caption_images) else [])
        # 图注与正文一起向量化：描述也要能被语义检索到（否则"图里写了什么"永远搜不出）
        contents = pieces + [c["content"] for c in image_chunks]
        vectors = embedder.embed(contents) if (embedder and contents) else [None] * len(contents)
        session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        rows = [Chunk(tenant_id=doc.tenant_id, document_id=doc.id, kb_id=doc.kb_id,
                      chunk_index=i, content=c, embedding=v, meta={})
                for i, (c, v) in enumerate(zip(pieces, vectors))]
        for j, ic in enumerate(image_chunks):
            rows.append(Chunk(tenant_id=doc.tenant_id, document_id=doc.id, kb_id=doc.kb_id,
                              chunk_index=len(pieces) + j, content=ic["content"],
                              embedding=vectors[len(pieces) + j], meta=ic["meta"]))
        session.add_all(rows)
        doc.status = "ready"
        doc.error = None
    except Exception as e:
        doc.status = "failed"
        doc.error = f"{e.__class__.__name__}: {e}"
    session.commit()
    return doc


def read_stored(doc: Document) -> bytes:
    return open(doc.storage_path, "rb").read()
