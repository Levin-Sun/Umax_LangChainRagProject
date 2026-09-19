# 阶段 0 收口实验：BM25 / +向量RRF / +重排 三档纯检索对比（不调 LLM）
# 指标：recall@5（期望文档进前5的比例）、MRR（期望文档排名倒数的均值）
import json
import sys
from pathlib import Path

import jieba
import pg8000.native
from rank_bm25 import BM25Okapi

sys.stdout.reconfigure(encoding="utf-8")

import config
import retrieve

EVAL_DIR = Path(__file__).parent / "eval"


def rank_of(target: str, hits: list[dict]) -> int | None:
    for i, h in enumerate(hits, 1):
        if h["doc_name"] == target:
            return i
    return None


def main() -> None:
    items = json.loads((EVAL_DIR / "golden_qa.json").read_text(encoding="utf-8"))
    chunks = retrieve._load_chunks(retrieve._conn())
    tokenized = [list(jieba.cut_for_search(c["content"])) for c in chunks]
    bm25 = BM25Okapi(tokenized)

    configs = ["BM25", "BM25+向量RRF", "BM25+向量RRF+重排"]
    stats = {c: {"hit": 0, "rr_sum": 0.0, "ranks": []} for c in configs}

    for it in items:
        q = it["q"]
        target = it["cites"][0]
        qtokens = list(jieba.cut_for_search(q))
        bm25_ranking = sorted(
            range(len(chunks)), key=lambda i: bm25.get_scores(qtokens)[i], reverse=True
        )[: config.RECALL_K]
        by_bm25 = [{**chunks[i], "score": bm25.get_scores(qtokens)[i]} for i in bm25_ranking]

        qvec = retrieve._embed_query(q)
        vec_ranking = sorted(
            range(len(chunks)),
            key=lambda i: retrieve._cosine(qvec, chunks[i]["vec"]) if chunks[i]["vec"] else -1.0,
            reverse=True,
        )[: config.RECALL_K]
        fused = retrieve._rrf_fuse([bm25_ranking, vec_ranking])
        order = sorted(fused, key=lambda i: fused[i], reverse=True)[: config.RECALL_K]
        by_rrf = [{**chunks[i], "rrf": fused[i]} for i in order]

        by_rerank = retrieve._rerank(q, by_rrf, config.RERANK_TOP_N)

        for name, hits in [("BM25", by_bm25[: config.RERANK_TOP_N]),
                           ("BM25+向量RRF", by_rrf[: config.RERANK_TOP_N]),
                           ("BM25+向量RRF+重排", by_rerank)]:
            r = rank_of(target, hits)
            if r:
                stats[name]["hit"] += 1
                stats[name]["rr_sum"] += 1.0 / r
                stats[name]["ranks"].append(r)
            else:
                stats[name]["ranks"].append(0)

    n = len(items)
    print(f"{'配置':<18}{'recall@5':>10}{'MRR':>8}   目标文档未进前5的题")
    for name in configs:
        s = stats[name]
        misses = [items[i]["id"] for i, r in enumerate(s["ranks"]) if r == 0]
        print(f"{name:<18}{s['hit']}/{n:>7}{s['rr_sum'] / n:>8.3f}   {misses or '无'}")


if __name__ == "__main__":
    main()
