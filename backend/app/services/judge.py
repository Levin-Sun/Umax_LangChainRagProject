# LLM 裁判（§阶段2「评测体系正式化」的 Ragas 侧）：判"有没有编造资料外的内容"。
#
# 关键词判据结构上看不到这个维度：答案里该出现的词都出现了，仍然可能夹带资料里没有的结论
# （最典型的失败：把常识当资料答）。faithfulness 就是抓这个。
#
# **裁判分数永不并入通过率**——这是本模块唯一的设计铁律。裁判模型自己会漂移、会随版本变松变紧，
# 把"更准"的总分建立在一个会变的东西上等于把证据变成噪声。所以两者分开呈现、各自可比：
#   通过率 / 检索指标 = 确定性判据（同配置跑两次必须一致）
#   faithfulness     = 裁判主观判断（换个裁判模型就会变，故必须连模型名一起记录）
import json
import re
from collections.abc import Callable

from app.services.text import clean_text

JUDGE_PROMPT = """你是严格的评测裁判，只依据【资料】判断【回答】，不使用资料之外的知识。

按两项各判 0 或 1：
- faithful：回答中的每个事实性结论都能在【资料】里找到依据。同义改写、归纳、把资料里的
  错别字读通都不算编造；**资料中没有的结论**（哪怕它是对的常识）算编造。
- relevance：回答切中问题本身，没有答非所问、没有只复述资料却不回答问题。

只输出一行 JSON，不要任何解释文字：
{"faithful": 0, "relevance": 0, "reason": "一句话说明扣分点"}"""

_JSON_RE = re.compile(r"\{.*\}", re.S)


def build_judge_prompt(question: str, answer: str, hits: list[dict]) -> str:
    corpus = "\n\n".join(f"[{i}] （来源：{h.get('doc_name', '?')}）\n{h.get('content', '')}"
                         for i, h in enumerate(hits, 1)) or "（无检索结果）"
    return f"【资料】\n{corpus}\n\n【问题】{question}\n\n【回答】{answer}"


def _score(v) -> int | None:
    """0/1 归一：模型爱输出 true/false、"是"/"否"、1.0——都收；实在认不出返回 None（不猜）。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return 1 if v >= 0.5 else 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "是", "符合"):
            return 1
        if s in ("0", "false", "no", "否", "不符合"):
            return 0
    return None


def parse_verdict(text: str) -> dict:
    """从裁判输出里抠 JSON。**永不抛异常**：裁判的输出格式不可控，解析失败要成为一条
    可归因的结果（faithful=None + 原始片段留档），而不是把整轮评测打断。"""
    raw = (text or "").strip()
    m = _JSON_RE.search(raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return {"faithful": _score(data.get("faithful")),
                        "relevance": _score(data.get("relevance")),
                        "reason": clean_text(str(data.get("reason") or ""))[:500], "raw": None}
        except json.JSONDecodeError:
            pass
    return {"faithful": None, "relevance": None,
            "reason": "裁判输出无法解析", "raw": clean_text(raw[:300])}


def make_judge_fn(complete: Callable[[list[dict]], dict]) -> Callable[[str, str, list[dict]], dict]:
    """把「一次 messages→回答」的调用适配成裁判协议：(question, answer, hits) -> 判词。

    complete 由调用方注入（网关的 gw.chat 或 .env 直连的 ChatClient.complete）——
    裁判与生成共用同一个模型通路，但**换裁判模型只需改后台那一行配置**。
    """

    def judge_fn(question: str, answer: str, hits: list[dict]) -> dict:
        out = complete([{"role": "system", "content": JUDGE_PROMPT},
                        {"role": "user", "content": build_judge_prompt(question, answer, hits)}])
        verdict = parse_verdict(out.get("text", ""))
        return {**verdict, "model": out.get("model"),
                "prompt_tokens": out.get("prompt_tokens") or 0,
                "completion_tokens": out.get("completion_tokens") or 0,
                "latency_ms": out.get("latency_ms")}

    return judge_fn


def judge_stats(items: list[dict]) -> dict:
    """汇总裁判结果（只统计真正判出来的题：解析失败的题不进分母，避免"裁判读不懂"被记成"回答不好"）。"""
    judged = [i["judge"] for i in items if i.get("judge")]
    scored = [j for j in judged if j.get("faithful") is not None]
    models = sorted({j["model"] for j in judged if j.get("model")})
    n = len(scored)
    return {
        "judged": len(judged), "scored": n,
        "faithful": sum(1 for j in scored if j["faithful"] == 1),
        "relevance": sum(1 for j in scored if j["relevance"] == 1),
        "faithful_rate": round(sum(1 for j in scored if j["faithful"] == 1) / n, 4) if n else 0.0,
        "relevance_rate": round(sum(1 for j in scored if j["relevance"] == 1) / n, 4) if n else 0.0,
        "model": models[0] if models else None,
    }
