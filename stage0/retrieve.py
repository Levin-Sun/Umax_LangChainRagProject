# 阶段 0：混合检索（BM25 + 向量 RRF 融合）+ 可选重排
import httpx
import jieba
import pg8000.native
from rank_bm25 import BM25Okapi

import config


def _conn() -> pg8000.native.Connection:
    return pg8000.native.Connection(
        host=config.PG["host"], port=config.PG["port"], user=config.PG["user"],
        password=config.PG["password"], database=config.PG["database"],
    )


def _load_chunks(con) -> list[dict]:
    rows = con.run("SELECT id, doc_name, chunk_index, content, embedding::text FROM chunks")
    return [
        {"id": r[0], "doc_name": r[1], "chunk_index": r[2], "content": r[3],
         "vec": [float(x) for x in r[4].strip("[]").split(",")] if r[4] else None}
        for r in rows
    ]


def _embed_query(query: str) -> list[float] | None:
    try:
        resp = httpx.post(
            f"{config.COMPAT_BASE}/embeddings",
            headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
            json={"model": config.EMBEDDING_MODEL, "input": [query],
                  "dimensions": config.EMBEDDING_DIM, "encoding_format": "float"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as e:
        print(f"⚠️ 向量化不可用（{e.__class__.__name__}），退回纯 BM25 检索")
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _rrf_fuse(rankings: list[list[int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (config.RRF_K + rank + 1)
    return scores


def _rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    """百炼原生 gte-rerank-v2（兼容模式不提供 rerank 端点）；不可用时退回融合排序。"""
    try:
        resp = httpx.post(
            f"{config.NATIVE_BASE}/services/rerank/text-rerank/text-rerank",
            headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
            json={"model": config.RERANK_MODEL,
                  "input": {"query": query, "documents": [c["content"] for c in candidates]},
                  "parameters": {"return_documents": False, "top_n": top_n}},
            timeout=60,
        )
        resp.raise_for_status()
        results = resp.json()["output"]["results"]
        return [
            {**candidates[r["index"]], "rerank_score": r["relevance_score"]}
            for r in results
        ]
    except Exception as e:
        print(f"⚠️ 重排不可用（{e.__class__.__name__}），使用 RRF 融合排序")
        return candidates[:top_n]


def retrieve(query: str, use_rerank: bool = True) -> tuple[list[dict], list[dict]]:
    """返回 (最终结果, 诊断信息)。结果按相关性排序，每项含 doc_name/content/得分。"""
    chunks = _load_chunks(_conn())
    if not chunks:
        raise RuntimeError("chunks 表为空，请先运行 ingest.py")

    # 两路召回
    tokenized = [list(jieba.cut_for_search(c["content"])) for c in chunks]
    bm25 = BM25Okapi(tokenized)
    bm25_ranking = sorted(
        range(len(chunks)), key=lambda i: bm25.get_scores(list(jieba.cut_for_search(query)))[i],
        reverse=True,
    )[: config.RECALL_K]

    qvec = _embed_query(query)
    if qvec is not None:
        vec_ranking = sorted(
            range(len(chunks)),
            key=lambda i: _cosine(qvec, chunks[i]["vec"]) if chunks[i]["vec"] else -1.0,
            reverse=True,
        )[: config.RECALL_K]
    else:
        vec_ranking = []

    fused = _rrf_fuse([bm25_ranking, vec_ranking])
    order = sorted(fused, key=lambda i: fused[i], reverse=True)[: config.RECALL_K]
    candidates = [
        {**chunks[i], "bm25_hit": i in bm25_ranking, "vec_hit": i in vec_ranking}
        for i in order
    ]

    if use_rerank:
        final = _rerank(query, candidates, config.RERANK_TOP_N)
    else:
        final = candidates[: config.RERANK_TOP_N]
    diag = {"bm25_hits": [chunks[i]["doc_name"] for i in bm25_ranking],
            "vec_hits": [chunks[i]["doc_name"] for i in vec_ranking]}
    return final, diag


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    q = sys.argv[1] if len(sys.argv) > 1 else "生鲜支持七天无理由退货吗？"
    hits, diag = retrieve(q)
    print(f"[BM25 命中] {diag['bm25_hits']}")
    print(f"[向量 命中] {diag['vec_hits']}")
    print(f"[最终 Top{len(hits)}]")
    for i, h in enumerate(hits, 1):
        score = h.get("rerank_score", "—")
        print(f"  [{i}] {h['doc_name']}#chunk{h['chunk_index']} score={score:.4f}"
              if isinstance(score, float) else f"  [{i}] {h['doc_name']}#chunk{h['chunk_index']}")
        print(f"      {h['content'][:80]}...")
