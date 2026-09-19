# 阶段 0：问答 CLI —— python query.py "你的问题"
import sys

sys.stdout.reconfigure(encoding="utf-8")

import retrieve
from generate import generate


def main() -> None:
    if len(sys.argv) < 2:
        print('用法：python query.py "问题" [--no-rerank]')
        sys.exit(1)
    use_rerank = "--no-rerank" not in sys.argv
    query = sys.argv[1]
    print(f"问题：{query}\n检索中（rerank={'开' if use_rerank else '关'}）...")
    hits, diag = retrieve.retrieve(query, use_rerank=use_rerank)
    print(f"[BM25] {diag['bm25_hits']}")
    print(f"[向量] {diag['vec_hits']}")
    result = generate(query, hits)
    print("\n=== 回答 ===")
    print(result["answer"])
    print("\n=== 来源 ===")
    for d in result["cited_docs"]:
        print(f" - {d}")
    print(f"\n[tokens] prompt={result['prompt_tokens']} completion={result['completion_tokens']}")


if __name__ == "__main__":
    main()
