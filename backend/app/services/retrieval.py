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
    # c.kb_id 一并出库：hits 带"出处库"id——RBAC 越权断言（test_permissions）与网关 kb_id
    # 记账都吃这个字段，缺它就只能靠 doc_name 反推出处，过滤正确性无法在数据层证明
    sql = ("SELECT c.id, d.name AS doc_name, c.kb_id, c.chunk_index, c.content"
           " FROM chunks c JOIN documents d ON d.id = c.document_id")
    params: dict = {}
    if kb_ids:
        sql += " WHERE c.kb_id = ANY(:kb_ids)"
        params["kb_ids"] = list(kb_ids)
    return [dict(zip(("id", "doc_name", "kb_id", "chunk_index", "content"), r))
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
    allowed_kb_ids: set[int] | None = None,
    recall_k: int = 10,
    top_k: int = 5,
    rrf_k: int = 60,
    min_sim: float = 0.15,
    rerank: Callable[[str, list[dict]], list[dict]] | None = None,
) -> list[dict]:
    from app.services.fusion import rrf_fuse  # 局部导入避免环依赖

    # 授权钳制在谓词层（spec 裁决 3）：None=admin 不限；集合（含空集）=可见库全集。
    # 交集而非替换：调用方点了未授权库要静默剔除，越权显式点库由端点先行 403。
    if allowed_kb_ids is not None:
        scope = (set(kb_ids) & allowed_kb_ids) if kb_ids else set(allowed_kb_ids)
        if not scope:
            return []           # 空授权=空结果——绝不落到"无过滤全库"
        kb_ids = sorted(scope)  # 两路（_load_chunks/BM25 语料 与 _vector_ranking）天然都走 ANY(:kb_ids) 谓词

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
