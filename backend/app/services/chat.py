# 带引用生成（迁自 stage0 generate.py）：OpenAI 兼容 chat，重试退避
import time
from collections.abc import Callable, Sequence

import httpx

from app.services.text import clean_text

RETRY_BACKOFF = [2, 5, 10]

SYSTEM_PROMPT = """你是企业知识库助手。回答规则：
1. 只依据【资料】回答，禁止使用资料之外的知识。
2. 资料可能含错别字、口语、中英混写，请理解后用通顺中文作答。
3. 每个结论后面标注来源编号，如 [1]；涉及多条资料分别标注。
4. 资料之间说法冲突时，明确指出冲突和各自出处，不要擅自裁决；资料标注"作废/旧版/待确认"的要说明。
5. 资料中没有答案时，明确回答"资料里没有相关内容"，不要编造。"""


def build_user_prompt(query: str, hits: Sequence[dict]) -> str:
    corpus = "\n\n".join(
        f"[{i}] （来源：{h['doc_name']}）\n{h['content']}" for i, h in enumerate(hits, 1)
    )
    return f"【资料】\n{corpus}\n\n【问题】{query}"


class ChatClient:
    def __init__(self, *, api_key: str, base_url: str, model: str,
                 temperature: float = 0.2,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 120):
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._temperature = temperature
        self._sleep = sleep

    def complete(self, messages: list[dict]) -> dict:
        payload = {"model": self._model, "temperature": self._temperature,
                   "messages": messages}
        last_err: Exception | None = None
        for wait in [0] + RETRY_BACKOFF:
            if wait:
                self._sleep(wait)
            try:
                resp = self._client.post(
                    f"{self._base}/chat/completions",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                return {
                    # 在客户端层收口：所有走 ChatClient 的路径（问答/识图/worker 图注）一并覆盖，
                    # 不必指望"每个调用点都记得清洗"（漏一处就是一次未声明 500）
                    "text": clean_text(data["choices"][0]["message"]["content"]),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                }
            except Exception as e:
                last_err = e
        raise last_err  # type: ignore[misc]


def resolve_prompt(prompt) -> str:
    """提示词可以是字符串，也可以是零参回调（运行时从配置中心现取——改完即生效，无需重启）。"""
    if callable(prompt):
        return prompt()
    return prompt or SYSTEM_PROMPT


def make_chat_fn(client: ChatClient, *, system_prompt=None) -> Callable[[str, list[dict]], dict]:
    """适配 API 层的 chat_fn 协议：(query, hits) -> {answer, prompt_tokens, completion_tokens}。
    system_prompt：None/字符串/零参回调（后台可改的系统提示词，见 services/settings）。"""

    def chat_fn(query: str, hits: list[dict]) -> dict:
        out = client.complete([
            {"role": "system", "content": resolve_prompt(system_prompt)},
            {"role": "user", "content": build_user_prompt(query, hits)},
        ])
        return {"answer": out["text"],
                "prompt_tokens": out["prompt_tokens"],
                "completion_tokens": out["completion_tokens"]}

    return chat_fn


# 视觉场景提示词（传图提问）：描述喂给检索与生成，非多模态向量检索的务实替代（§3.2③）
VISION_PROMPT = ("请用简洁中文描述这张图片：图表/流程图要写出关键节点与数字，"
                 "截图要转写可见文字，照片要说明主体。供知识库检索与问答使用。")
