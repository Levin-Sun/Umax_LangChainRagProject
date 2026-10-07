# 评测内核（§阶段2「评测体系正式化」）：金标准问答集 → 判据 → 分数 → Markdown 报告。
#
# 两个刻意的设计选择：
# ① **判据全确定性**（关键词命中 / 引用正确 / 金标准文档的检索位次），不引入裁判模型。
#    裁判模型自己会漂移、会随版本变松变紧，"更准"的证据不能建立在一个会变的东西上。
#    关键词判据的代价是只能验证"该出现的答了没"，验证不了"有没有编造资料外的内容"——
#    那一层留给后续的裁判模型（Ragas 类），本模块只保证**每次跑都可比**。
# ② **passed 语义与 archive/stage0/eval.py 逐字一致**（kw_all ∧ kw_any ∧ citation）。
#    新体系的分数必须能直接与历史报告对照，否则"防退化"就退化成两套互不相干的数字。
#    检索指标（hit_rate / mrr）单独出，不并进通过率：检索没命中而生成答对了是真实存在的
#    （答案可能来自别的块），两者混在一起就无法归因到底是检索还是生成该调。
from collections.abc import Sequence
from pathlib import Path

__all__ = ["check_item", "rank_of_expected", "aggregate", "render_report",
           "load_golden", "GOLDEN_FILE"]

# 预置金标准集（与 archive/stage0/eval/golden_qa.json 同源）：随应用打包，
# 首次启动播种进 eval_questions——"评测集第一天就建"这条风险对策要开箱即生效，
# 而不是让客户先自己攒题（攒题这件事客户一定拖到永远）。
GOLDEN_FILE = Path(__file__).resolve().parents[1] / "eval" / "golden_qa.json"

_ITEM_FIELDS = ("question", "expect_all", "expect_any", "cites", "category", "note")


def load_golden(path: Path | None = None) -> list[dict]:
    """读预置金标准集，归一成 EvalQuestion 的字段名（stage0 用短键 q/id，其余同名）。

    缺字段一律补空值而不是报错：预置集是"起手牌"，客户会改会删，
    少一个 note 不该让整个播种失败（更不该让部署启动不起来）。
    """
    import json

    raw = json.loads((path or GOLDEN_FILE).read_text(encoding="utf-8"))
    out = []
    for item in raw:
        question = item.get("question") or item.get("q") or ""
        if not question.strip():
            continue          # 没有问题的行不是题：跳过而不是插一条空题进来
        row = {k: item.get(k) for k in _ITEM_FIELDS}
        row["question"] = question
        row["expect_all"] = list(row["expect_all"] or [])
        row["expect_any"] = list(row["expect_any"] or [])
        row["cites"] = list(row["cites"] or [])
        row["category"] = row["category"] or ""
        out.append(row)
    return out


def rank_of_expected(top_docs: Sequence[str], cites: Sequence[str]) -> int:
    """金标准文档在召回列表里的首个位次（1-based）；0=未命中。

    top_docs 按相关度降序——位次的倒数即该题的检索贡献（MRR 的分子）。
    无金标准（cites 为空）时返回 0，调用方负责把它从检索指标的分母里剔除。
    """
    wanted = set(cites or ())
    for i, name in enumerate(top_docs or (), 1):
        if name in wanted:
            return i
    return 0


def check_item(item: dict, *, answer: str | None, cited_docs: Sequence[str],
               top_docs: Sequence[str]) -> dict:
    """单题判据。返回的每个键都可独立归因——报告里"败在哪一项"必须一眼可见。

    retrieval=None 表示该题没设金标准文档（无检索指标可言，不是失败）。
    """
    ans = answer or ""
    expect_all = item.get("expect_all") or []
    expect_any = item.get("expect_any") or []
    cites = item.get("cites") or []
    kw_all = all(k in ans for k in expect_all)
    kw_any = (not expect_any) or any(k in ans for k in expect_any)
    cited = set(cited_docs or ())
    citation = (not cites) or any(c in cited for c in cites)
    rank = rank_of_expected(top_docs, cites)
    # 生成失败（answer=None）绝不算过：没有期望词的题目若不特判，"什么都没答"会白捡一分
    passed = answer is not None and kw_all and kw_any and citation
    return {"kw_all": kw_all, "kw_any": kw_any, "citation": citation,
            "retrieval": None if not cites else rank > 0, "rank": rank, "passed": passed}


def aggregate(results: Sequence[dict]) -> dict:
    """汇总指标。results 每项需带 category/passed/rank/latency_ms/cites。

    分数保留 4 位小数：报告要能贴进交付文档，长浮点尾巴只会让人怀疑精度。
    """
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    with_cites = [r for r in results if r.get("cites")]
    hit = sum(1 for r in with_cites if r["rank"] > 0)
    # MRR：未命中记 0（而不是剔出分母）——"检索不到"本身就是最差的相关度，不该被平均掉
    mrr = (sum(1.0 / r["rank"] for r in with_cites if r["rank"] > 0) / len(with_cites)
           if with_cites else 0.0)
    by_cat: dict[str, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r.get("category") or "未分类", []).append(bool(r["passed"]))
    return {
        "total": total, "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "with_cites": len(with_cites), "hit": hit,
        "hit_rate": round(hit / len(with_cites), 4) if with_cites else 0.0,
        "mrr": round(mrr, 4),
        "avg_latency_ms": int(sum(r.get("latency_ms") or 0 for r in results) / total)
        if total else 0,
        "categories": [{"category": c, "total": len(v), "passed": sum(v)}
                       for c, v in sorted(by_cat.items())],
    }


def render_report(*, meta: dict, metrics: dict, items: Sequence[dict]) -> str:
    """Markdown 报告：给客户看的"白纸黑字"，也是两次调参之间做 diff 的载体。

    metrics 允许为空（运行中 / 整轮失败时调 /report）：缺键一律按 0 兜底而不是 KeyError——
    查看一个还没跑完的评测不该收到 500，该收到一份"目前 0 分、明细为空"的报告。
    """
    metrics = {"total": 0, "passed": 0, "pass_rate": 0.0, "with_cites": 0, "hit": 0,
               "hit_rate": 0.0, "mrr": 0.0, "avg_latency_ms": 0, "categories": [],
               **(metrics or {})}
    lines = ["# 评测报告", ""]
    if meta.get("time"):
        lines.append(f"- 时间：{meta['time']}")
    if meta.get("kb_scope"):
        lines.append(f"- 范围：{meta['kb_scope']}")
    models = "，".join(f"{k}={v}" for k, v in (("chat", meta.get("chat_model")),
                                              ("embedding", meta.get("embedding_model")))
                       if v)
    if models:
        lines.append(f"- 模型：{models}")
    lines.append(f"- 题数：{metrics['total']}")
    lines += [
        "",
        f"## 总分：{metrics['passed']}/{metrics['total']}（{metrics['pass_rate']:.0%}）",
        "",
        "| 指标 | 值 |", "|---|---|",
        f"| 通过率 | {metrics['pass_rate']:.0%} |",
        f"| 检索命中率（有金标准的 {metrics['with_cites']} 题） | "
        f"{metrics['hit']}/{metrics['with_cites']}（{metrics['hit_rate']:.0%}） |",
        f"| MRR | {metrics['mrr']:.3f} |",
        f"| 平均耗时 | {metrics['avg_latency_ms']} ms |",
        "",
        "| 考察点 | 通过 | 小计 |", "|---|---|---|",
    ]
    for c in metrics["categories"]:
        lines.append(f"| {c['category']} | {c['passed']}/{c['total']} | {c['total']} |")
    lines += ["", "## 明细", ""]
    for i, r in enumerate(items, 1):
        checks = r.get("checks") or {}
        mark = "✅" if checks.get("passed") else "❌"
        lines.append(f"### {mark} [{r.get('id') or i}] {r.get('question', '')}")
        lines.append(f"- 考察点：{r.get('category') or '未分类'} —— {r.get('note') or ''}")
        lines.append(f"- 命中Top5：{'、'.join(r.get('top_docs') or []) or '无'}")
        if r.get("cites"):
            lines.append(f"- 金标准文档位次：{r.get('rank') or '未命中'}")
        lines.append(f"- 引用：{'、'.join(r.get('cited_docs') or []) or '无'}")
        lines.append(f"- 判据：关键词(全)={'✅' if checks.get('kw_all') else '❌'} "
                     f"关键词(任一)={'✅' if checks.get('kw_any') else '❌'} "
                     f"引用={'✅' if checks.get('citation') else '❌'}"
                     + ("" if checks.get("retrieval") is None
                        else f" 检索={'✅' if checks.get('retrieval') else '❌'}"))
        if r.get("answer"):
            lines.append(f"- 回答：\n```\n{r['answer']}\n```")
        if r.get("error"):
            lines.append(f"- 错误：{r['error']}")
        lines.append("")
    return "\n".join(lines)
