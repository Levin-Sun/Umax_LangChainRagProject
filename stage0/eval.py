# 阶段 0：金标准评测 —— python eval.py
# 对 golden_qa.json 每题跑完整链路，校验：答案关键词、引用文档是否正确，产出基线报告。
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import config
from generate import generate
import retrieve

EVAL_DIR = Path(__file__).parent / "eval"


def check(item: dict, answer: str, cited: list[str]) -> dict:
    ok_all = all(k in answer for k in item.get("expect_all", []))
    any_list = item.get("expect_any", [])
    ok_any = (not any_list) or any(k in answer for k in any_list)
    expected_cites = item.get("cites", [])
    ok_cite = (not expected_cites) or any(c in cited for c in expected_cites)
    return {
        "pass": ok_all and ok_any and ok_cite,
        "kw_all": ok_all, "kw_any": ok_any, "citation": ok_cite,
    }


def main() -> None:
    items = json.loads((EVAL_DIR / "golden_qa.json").read_text(encoding="utf-8"))
    print(f"金标准 {len(items)} 题，模型 {config.CHAT_MODEL} / {config.EMBEDDING_MODEL}\n")
    results = []
    for it in items:
        t0 = time.time()
        try:
            hits, _ = retrieve.retrieve(it["q"], use_rerank=True)
            top_docs = [h["doc_name"] for h in hits]
            gen = generate(it["q"], hits)
            checks = check(it, gen["answer"], gen["cited_docs"])
            err = None
        except Exception as e:  # 单题失败不中断评测
            top_docs, gen, checks, err = [], None, {"pass": False, "kw_all": False, "kw_any": False, "citation": False}, repr(e)
        cost = round(time.time() - t0, 1)
        results.append({**it, "top_docs": top_docs, "answer": gen, "checks": checks,
                        "seconds": cost, "error": err})
        mark = "✅ PASS" if checks["pass"] else "❌ FAIL"
        flag = "" if checks["pass"] else f"  (kw_all={checks['kw_all']} kw_any={checks['kw_any']} cite={checks['citation']})"
        print(f"[{it['id']:>2}] {mark}  {it['q']}  ({cost}s){flag}")

    n_pass = sum(1 for r in results if r["checks"]["pass"])
    by_cat: dict[str, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["checks"]["pass"])

    report = [
        "# 阶段 0 基线评测报告",
        f"\n- 时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 模型：chat={config.CHAT_MODEL}, embedding={config.EMBEDDING_MODEL}, rerank={config.RERANK_MODEL}",
        f"- 检索：BM25 + 向量 RRF 融合 → gte-rerank 重排 Top{config.RERANK_TOP_N}",
        f"- 语料：DirtyDocs {20} 份电商脏文档（错别字/重复/版本冲突/噪声/中英混杂）",
        f"\n## 总分：{n_pass}/{len(results)}（{n_pass / len(results):.0%}）\n",
        "| 考察点 | 通过 | 小计 |", "|---|---|---|",
    ]
    for cat, oks in sorted(by_cat.items()):
        report.append(f"| {cat} | {sum(oks)}/{len(oks)} | {len(oks)} |")
    report.append("\n## 明细\n")
    for r in results:
        mark = "✅" if r["checks"]["pass"] else "❌"
        report.append(f"### {mark} [{r['id']}] {r['q']}")
        report.append(f"- 考察点：{r['category']} —— {r['note']}")
        report.append(f"- 命中Top5：{', '.join(r['top_docs'])}")
        if r.get("answer"):
            report.append(f"- 引用：{', '.join(r['answer']['cited_docs']) or '无'}")
            report.append(f"- 回答：\n```\n{r['answer']['answer']}\n```")
        else:
            report.append(f"- 错误：{r['error']}")
        report.append("")
    out = EVAL_DIR / f"report_{datetime.now():%Y%m%d_%H%M}.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\n总分 {n_pass}/{len(results)}（{n_pass / len(results):.0%}）")
    print(f"分类明细：\n" + "\n".join(f"  {c}: {sum(o)}/{len(o)}" for c, o in sorted(by_cat.items())))
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
