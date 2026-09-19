# 阶段 0：带引用的答案生成
import re

import httpx

import config

SYSTEM_PROMPT = """你是企业电商知识库助手。回答规则：
1. 只依据【资料】回答，禁止使用资料之外的知识。
2. 资料可能来自运营手记，含错别字（如"流成"="流程"、"质亮"="质量"、"赔长"="赔偿"）、口语、英文混写，请理解后用通顺中文作答。
3. 每个结论后面标注来源编号，如 [1]；结论涉及多条资料时分别标注。
4. 资料之间说法冲突时，明确指出冲突和各自出处，不要擅自裁决；如果资料自己标注了"作废/旧版/待确认"，要说明。
5. 资料中没有答案时，明确回答"资料里没有相关内容"，不要编造。"""


def generate(query: str, hits: list[dict]) -> dict:
    corpus = "\n\n".join(
        f"[{i}] （来源：{h['doc_name']}）\n{h['content']}" for i, h in enumerate(hits, 1)
    )
    resp = httpx.post(
        f"{config.COMPAT_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
        json={
            "model": config.CHAT_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"【资料】\n{corpus}\n\n【问题】{query}"},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    answer = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    cited_docs = sorted({hits[int(n) - 1]["doc_name"] for n in re.findall(r"\[(\d+)\]", answer)
                         if 1 <= int(n) <= len(hits)})
    return {"answer": answer, "cited_docs": cited_docs,
            "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)}
