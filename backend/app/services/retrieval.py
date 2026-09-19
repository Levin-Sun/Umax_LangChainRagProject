# 混合检索服务：BM25 + pgvector 向量两路召回 → RRF 融合 → 可选重排
# （stage0 retrieve.py 的服务化：向量距离交给 pgvector SQL，块与文档名从库中 join）
from collections.abc import Callable, Sequence
from typing import Protocol

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _load_chunks(session: Session, kb_ids: list[int] | None) -> list[dict]:
    sql = ("SELECT c.id, d.name AS doc_name, c.chunk_index, c.content"
           " FROM chunks c JOIN documents d ON d.id = c.document_id")
    params: dict = {}
    if kb_ids:
        sql += " WHERE c.kb_id = ANY(:kb_ids)"
        params["kb_ids"] = list(kb_ids)
    return [dict(zip(("id", "doc_name", "chunk_index", "content"), r))
            for r in session.execute(sa_text(sql), params)]


def _bm25_ranking(chunks: list[dict], query: str, limit: int) -> list[int]:
    corpus = [list(jieba.cut_for_search(c["content"])) for c in chunks]
    model = BM25Okapi(corpus)
    scores = model.get_scores(list(jieba.cut_for_search(query)))
    order = sorted(range(len(chunks)), key=lambda i: -scores[i])
    # 小语料下 rank_bm25 的 idf 可为负（词项覆盖全部文档），仅剔除零分（无信号）
    return [chunks[i]["id"] for i in order[:limit] if scores[i] != 0]


def _vector_ranking(session: Session, qvec: list[float],
                    kb_ids: list[int] | None, limit: int,
                    min_sim: float = 0.0) -> list[int]:
    sql = ("SELECT id FROM chunks WHERE embedding IS NOT NULL"
           " AND 1 - (embedding <=> CAST(:q AS vector)) >= :min_sim"
           + (" AND kb_id = ANY(:kb_ids)" if kb_ids else "")
           + " ORDER BY embedding <=> CAST(:q AS vector) LIMIT :limit")
    params = {"q": "[" + ",".join(f"{x:.6f}" for x in qvec) + "]", "limit": limit,
              "min_sim": min_sim}
    if kb_ids:
        params["kb_ids"] = list(kb_ids)
    return [r[0] for r in session.execute(sa_text(sql), params)]


def retrieve(
    session: Session,
    query: str,
    *,
    embedder: Embedder | None = None,
    kb_ids: list[int] | None = None,
    recall_k: int = 10,
    top_k: int = 5,
    rrf_k: int = 60,
    min_sim: float = 0.15,
    rerank: Callable[[str, list[dict]], list[dict]] | None = None,
) -> list[dict]:
    from app.services.fusion import rrf_fuse  # 局部导入避免环依赖

    chunks = _load_chunks(session, kb_ids)
    if not chunks:
        return []
    by_id = {c["id"]: c for c in chunks}

    bm25_ids = _bm25_ranking(chunks, query, recall_k)
    vec_ids: list[int] = []
    if embedder is not None:
        qvec = embedder.embed([query])[0]
        vec_ids = _vector_ranking(session, qvec, kb_ids, recall_k, min_sim)

    fused = rrf_fuse([bm25_ids, vec_ids], k=rrf_k)
    order = sorted(fused, key=lambda i: -fused[i])[:recall_k]
    candidates = [
        {**by_id[i], "score": fused[i],
         "bm25_hit": i in bm25_ids, "vec_hit": i in vec_ids}
        for i in order
    ]
    final = rerank(query, candidates) if rerank else candidates
    return final[:top_k]
