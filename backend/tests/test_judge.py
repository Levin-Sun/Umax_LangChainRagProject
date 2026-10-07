# LLM 裁判（Ragas 侧）：判词解析的健壮性 + 适配协议。
# 裁判的输出格式不可控（会加解释、会用 ```json、会写 true/是），解析必须能扛住，
# 且**永不抛异常**——解析失败是一条可归因的结果，不是把整轮评测打断的理由。
from app.services.judge import (JUDGE_PROMPT, build_judge_prompt, judge_stats,
                                make_judge_fn, parse_verdict)

HITS = [{"doc_name": "rag_dirty_doc_01.txt", "content": "生鲜不支持七天无理由退货。"}]


def test_parse_verdict_accepts_plain_and_messy_shapes():
    assert parse_verdict('{"faithful": 1, "relevance": 1, "reason": "有依据"}')["faithful"] == 1
    # 会加解释、会包 ```json —— 只取第一段 JSON
    messy = '好的，我的判断如下：\n```json\n{"faithful": 0, "relevance": 1, "reason": "编造了45天"}\n```\n以上。'
    got = parse_verdict(messy)
    assert (got["faithful"], got["relevance"], got["reason"]) == (0, 1, "编造了45天")
    # 布尔与中文口径都收（模型不听话是常态）
    assert parse_verdict('{"faithful": true, "relevance": "是", "reason": ""}')["relevance"] == 1
    assert parse_verdict('{"faithful": 1.0, "relevance": 0, "reason": ""}')["faithful"] == 1


def test_parse_verdict_never_raises_and_keeps_raw_for_triage():
    got = parse_verdict("我不会打分")
    assert got["faithful"] is None and got["relevance"] is None
    assert got["raw"] == "我不会打分"          # 留原始片段，排查"裁判到底说了啥"
    assert parse_verdict(None)["reason"] == "裁判输出无法解析"
    # 认不出的取值不猜：宁可 None（不进分母），也不默认记 0（那会把解析问题算成回答不好）
    assert parse_verdict('{"faithful": "maybe", "relevance": 1}')["faithful"] is None


def test_build_judge_prompt_carries_material_question_and_answer():
    p = build_judge_prompt("生鲜能退吗", "不支持 [1]", HITS)
    assert "生鲜不支持七天无理由退货。" in p and "[1] （来源：rag_dirty_doc_01.txt）" in p
    assert "【问题】生鲜能退吗" in p and "【回答】不支持 [1]" in p
    assert "（无检索结果）" in build_judge_prompt("q", "a", [])   # 未命中也要能判（判"没答"）


def test_make_judge_fn_adapts_completion_call_and_reports_usage():
    seen = {}

    def fake_complete(messages):
        seen["messages"] = messages
        return {"text": '{"faithful": 1, "relevance": 0, "reason": "没回答问题"}',
                "prompt_tokens": 321, "completion_tokens": 12, "model": "judge-model"}

    out = make_judge_fn(fake_complete)("生鲜能退吗", "不支持 [1]", HITS)
    assert out["faithful"] == 1 and out["relevance"] == 0
    assert out["model"] == "judge-model" and out["prompt_tokens"] == 321
    # 裁判用的系统提示词是模块常量（改口径改一处）
    assert seen["messages"][0] == {"role": "system", "content": JUDGE_PROMPT}


def test_judge_stats_excludes_unparsed_and_names_the_judge_model():
    items = [
        {"judge": {"faithful": 1, "relevance": 1, "model": "m1"}},
        {"judge": {"faithful": 0, "relevance": 1, "model": "m1"}},
        {"judge": {"faithful": None, "relevance": None, "model": "m1"}},   # 解析失败：不进分母
        {"judge": None},                                                   # 没判：不算
    ]
    s = judge_stats(items)
    assert (s["judged"], s["scored"]) == (3, 2)
    assert (s["faithful"], s["relevance"]) == (1, 2)
    assert (s["faithful_rate"], s["relevance_rate"]) == (0.5, 1.0)
    assert s["model"] == "m1"      # 裁判分数必须连模型名一起记：换模型就会变，不记就没法比


def test_judge_stats_on_empty_run_is_zero_not_a_division_by_zero():
    assert judge_stats([]) == {"judged": 0, "scored": 0, "faithful": 0, "relevance": 0,
                               "faithful_rate": 0.0, "relevance_rate": 0.0, "model": None}
