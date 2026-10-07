# 评测内核（§阶段2「评测体系正式化」）：金标准 → 判据 → 分数 → 报告。
# 判据全部确定性（关键词/引用/检索位次），不依赖裁判模型——**跑一次就有可比数字**是本设计的硬要求：
# 裁判模型自己会漂移，用它当唯一标尺等于把"更准"的证据建立在一个会变的东西上。
# passed 的语义与 stage0 eval.py 逐字一致（kw_all ∧ kw_any ∧ citation），
# 这样新体系产出的分数能直接与 archive/stage0/eval/ 下的历史报告对比，不退化成两套分数。
from app.services.evaluation import (GOLDEN_FILE, aggregate, check_item, load_golden,
                                     rank_of_expected, render_report)

ITEM = {"id": 1, "question": "生鲜能七天无理由退货吗", "expect_all": ["七天无理由"],
        "expect_any": ["不支持", "坏果"], "cites": ["rag_dirty_doc_01.txt"],
        "category": "错别字干扰", "note": "文档含错别字；答出'不支持'即过"}


# ---- rank_of_expected：期望文档在召回列表里的首个位次（1-based；0=没命中）----
def test_rank_of_expected_reports_first_hit_position():
    docs = ["a.txt", "b.txt", "c.txt"]
    assert rank_of_expected(docs, ["c.txt"]) == 3
    assert rank_of_expected(docs, ["b.txt", "c.txt"]) == 2    # 多篇金标准取最靠前的
    assert rank_of_expected(docs, ["zz.txt"]) == 0            # 召回里没有=未命中
    assert rank_of_expected([], ["a.txt"]) == 0
    assert rank_of_expected(docs, []) == 0                     # 无金标准：调用方从指标分母里剔除


# ---- check_item：四项判据各自独立可归因（失败时必须一眼看出败在哪一项）----
def test_check_item_all_checks_pass():
    got = check_item(ITEM, answer="生鲜不支持七天无理由，坏果按比例赔偿 [1]",
                     cited_docs=["rag_dirty_doc_01.txt"],
                     top_docs=["rag_dirty_doc_01.txt", "x.txt"])
    assert got == {"kw_all": True, "kw_any": True, "citation": True,
                   "retrieval": True, "rank": 1, "passed": True}


def test_check_item_attributes_each_kind_of_failure():
    # 关键词缺 1 个：expect_all 未满足（expect_any 里有"不支持"也救不回来）
    got = check_item(ITEM, answer="生鲜按比例赔偿，不支持坏果 [1]",
                     cited_docs=["rag_dirty_doc_01.txt"], top_docs=["rag_dirty_doc_01.txt"])
    assert got["kw_all"] is False and got["kw_any"] is True and got["passed"] is False
    # 引用了别的文档：citation 失败（答案对了但出处不对，溯源就假了）
    got = check_item(ITEM, answer="不支持七天无理由 [1]", cited_docs=["other.txt"],
                     top_docs=["rag_dirty_doc_01.txt"])
    assert got["citation"] is False and got["passed"] is False
    # 检索没拿到金标准文档：retrieval=False 但 rank=0——生成层仍可能答对（答案来自别的块），
    # 这正是"检索指标"与"通过率"必须分开看的原因
    got = check_item(ITEM, answer="生鲜不支持七天无理由 [1]",
                     cited_docs=["rag_dirty_doc_01.txt"], top_docs=["noise.txt"])
    assert got["retrieval"] is False and got["rank"] == 0 and got["passed"] is True


def test_check_item_treats_missing_expectations_as_met_but_empty_answer_as_failure():
    bare = {**ITEM, "expect_all": [], "expect_any": [], "cites": []}
    got = check_item(bare, answer="随便答", cited_docs=[], top_docs=[])
    assert got["passed"] is True and got["retrieval"] is None   # 无金标准=该题不计检索指标
    # 生成失败（answer=None）绝不算过：没有期望词时若不特判，"什么都没答"会白捡一分
    got = check_item(bare, answer=None, cited_docs=[], top_docs=[])
    assert got["passed"] is False


# ---- aggregate：通过率 / 检索命中率 / MRR / 分类明细 ----
def test_aggregate_computes_rates_and_mrr():
    results = [
        {"category": "错别字干扰", "passed": True, "rank": 1, "latency_ms": 100, "cites": ["a"]},
        {"category": "错别字干扰", "passed": False, "rank": 2, "latency_ms": 300, "cites": ["b"]},
        {"category": "版本冲突", "passed": True, "rank": 0, "latency_ms": 200, "cites": ["c"]},
        {"category": "版本冲突", "passed": True, "rank": 0, "latency_ms": 200, "cites": []},
    ]
    m = aggregate(results)
    assert (m["total"], m["passed"], m["pass_rate"]) == (4, 3, 0.75)
    # 检索指标只在有金标准的题上算：4 题里 1 题无 cites → 分母 3，命中 2 题
    assert (m["with_cites"], m["hit"], m["hit_rate"]) == (3, 2, 0.6667)
    assert m["mrr"] == 0.5      # (1/1 + 1/2 + 0) / 3
    assert m["avg_latency_ms"] == 200
    # 分类按名称码点序（确定性优先于"看着顺眼"：报告要能逐行 diff 两次跑的结果）
    assert m["categories"] == [
        {"category": "版本冲突", "total": 2, "passed": 2},
        {"category": "错别字干扰", "total": 2, "passed": 1}]


def test_aggregate_on_empty_run_is_all_zero_not_a_division_by_zero():
    m = aggregate([])
    assert m == {"total": 0, "passed": 0, "pass_rate": 0.0, "with_cites": 0, "hit": 0,
                 "hit_rate": 0.0, "mrr": 0.0, "avg_latency_ms": 0, "categories": []}


# ---- render_report：Markdown 报告（客户要能拿到"白纸黑字"，也要能与 stage0 报告对照）----
def test_render_report_carries_scores_and_per_question_detail():
    md = render_report(
        meta={"time": "2026-10-08 12:00", "kb_scope": "全部知识库",
              "chat_model": "qwen3.7-flash", "embedding_model": "qwen3.7-text-embedding"},
        metrics=aggregate([{"category": "错别字干扰", "passed": True, "rank": 1,
                            "latency_ms": 100, "cites": ["a"]}]),
        items=[{**ITEM, "answer": "生鲜不支持七天无理由 [1]",
                "cited_docs": ["rag_dirty_doc_01.txt"], "top_docs": ["rag_dirty_doc_01.txt"],
                "checks": {"kw_all": True, "kw_any": True, "citation": True, "retrieval": True},
                "latency_ms": 100, "error": None}])
    assert "# 评测报告" in md
    assert "1/1（100%）" in md                     # 总分
    assert "| 错别字干扰 | 1/1 | 1 |" in md        # 分类明细表
    assert "生鲜能七天无理由退货吗" in md           # 逐题明细
    assert "生鲜不支持七天无理由 [1]" in md         # 回答原文
    assert "rag_dirty_doc_01.txt" in md            # 命中与引用


def test_packaged_golden_set_loads_and_maps_fields():
    """预置金标准集随应用打包（与 stage0 同源）：字段要能原样映射进 eval_questions。
    这条测试同时是"20 题还在、没被谁误删"的守卫。"""
    assert GOLDEN_FILE.exists(), "预置金标准集缺失：部署后客户打开评测页会是空的"
    items = load_golden()
    assert len(items) == 20
    assert set(items[0]) == {"question", "expect_all", "expect_any", "cites",
                             "category", "note"}
    assert items[0]["question"].startswith("生鲜")     # stage0 用短键 q，须归一成 question
    assert items[0]["cites"] == ["rag_dirty_doc_01.txt"]
    assert all(isinstance(i["expect_all"], list) and i["question"].strip() for i in items)


def test_load_golden_tolerates_messy_source(tmp_path):
    """预置集是给人改的：空题跳过、缺字段补空，绝不因为一行残缺让部署起不来。"""
    import json

    p = tmp_path / "g.json"
    p.write_text(json.dumps([{"q": "   "}, {"q": "有题"}, {"id": 3}], ensure_ascii=False),
                 encoding="utf-8")
    items = load_golden(p)
    assert [i["question"] for i in items] == ["有题"]
    assert items[0]["expect_all"] == [] and items[0]["category"] == "" and items[0]["note"] is None


def test_render_report_tolerates_run_still_in_flight():
    """运行中/整轮失败时点"看报告"不该 500：缺指标按 0 兜底，明细为空即成。"""
    md = render_report(meta={"time": "t", "kb_scope": "全部知识库"}, metrics={}, items=[])
    assert "0/0（0%）" in md and "# 评测报告" in md


def test_render_report_marks_failures_and_errors():
    md = render_report(
        meta={"time": "t", "kb_scope": "全部知识库"},
        metrics=aggregate([{"category": "缺据", "passed": False, "rank": 0,
                            "latency_ms": 0, "cites": ["a"]}]),
        items=[{**ITEM, "answer": None, "cited_docs": [], "top_docs": [],
                "checks": {"kw_all": False, "kw_any": False, "citation": False,
                           "retrieval": False},
                "latency_ms": 0, "error": "GatewayError: 全部模型不可用"}])
    assert "❌" in md and "0/1（0%）" in md
    assert "GatewayError" in md     # 单题失败原因必须落在报告里（stage0 的教训：先看是不是瞬时错误，别急着调参数）
