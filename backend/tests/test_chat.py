# TDD 红灯：chat 服务（OpenAI 兼容 /chat/completions，重试退避，迁自 stage0 generate.py）
import json

import httpx
import pytest

from app.services.chat import RETRY_BACKOFF, ChatClient, build_user_prompt


def _transport(seq):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        item = seq[min(len(calls) - 1, len(seq) - 1)]
        if item == "boom":
            raise httpx.ConnectError("reset")
        return httpx.Response(item[0], json=item[1])

    return httpx.MockTransport(handler), calls


OK = (200, {"choices": [{"message": {"content": "答案[1]"}}],
            "usage": {"prompt_tokens": 630, "completion_tokens": 120}})


def _client(seq, calls_holder=None):
    tp, calls = _transport(seq)
    return ChatClient(api_key="k", base_url="https://x/v1", model="m",
                      transport=tp, sleep=lambda s: None), calls


def test_complete_returns_text_and_usage():
    c, calls = _client([OK])
    out = c.complete([{"role": "user", "content": "hi"}])
    assert out == {"text": "答案[1]", "prompt_tokens": 630, "completion_tokens": 120}
    assert calls[0]["model"] == "m" and calls[0]["temperature"] == 0.2


def test_retry_then_success():
    c, calls = _client(["boom", OK])
    assert c.complete([{"role": "user", "content": "hi"}])["text"] == "答案[1]"
    assert len(calls) == 2


def test_gives_up_after_all_backoffs():
    c, calls = _client([(500, {})])
    with pytest.raises(httpx.HTTPStatusError):
        c.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == len(RETRY_BACKOFF) + 1


def test_user_prompt_numbers_materials_for_citation():
    hits = [{"doc_name": "a.pdf", "content": "甲内容"},
            {"doc_name": "b.pdf", "content": "乙内容"}]
    p = build_user_prompt("能退吗？", hits)
    assert "[1] （来源：a.pdf）" in p and "[2] （来源：b.pdf）" in p
    assert p.rstrip().endswith("【问题】能退吗？")
