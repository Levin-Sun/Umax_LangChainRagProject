# TDD：百炼嵌入 provider——契约用假 transport 测，真调用只留冒烟
import json

import httpx
import pytest

from app.core.config import get_settings
from app.services.embeddings import RETRY_BACKOFF, BailianEmbedder


def _transport(replies: list[tuple[int, dict]]):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        code, payload = replies[min(len(calls) - 1, len(replies) - 1)]
        if code == 0:
            raise httpx.ConnectError("boom")
        if payload == "auto":  # 按批大小生成确定性向量
            n = len(body["input"])
            payload = {"data": [
                {"index": i, "embedding": [0.1 * (i + 1)] * 4} for i in range(n)
            ]}
        return httpx.Response(code, json=payload)

    return httpx.MockTransport(handler), calls


def test_batches_of_10_in_order():
    tp, calls = _transport([(200, "auto")])
    emb = BailianEmbedder(api_key="k", base_url="https://x/v1", model="m",
                          transport=tp)
    out = emb.embed([f"t{i}" for i in range(12)])
    assert [len(c["input"]) for c in calls] == [10, 2]
    assert len(out) == 12 and out[0][0] == 0.1 and out[3][0] == 0.4


def test_retry_backoff_then_success():
    tp, calls = _transport([(0, {}), (200, "auto")])
    emb = BailianEmbedder(api_key="k", base_url="https://x/v1", model="m",
                          transport=tp, sleep=lambda s: None)
    out = emb.embed(["a", "b", "c"])
    assert len(out) == 3 and len(calls) == 2


def test_gives_up_after_retries():
    tp, calls = _transport([(403, {"error": "denied"})])
    emb = BailianEmbedder(api_key="k", base_url="https://x/v1", model="m",
                          transport=tp, sleep=lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        emb.embed(["a"])
    assert len(calls) == len(RETRY_BACKOFF) + 1


@pytest.mark.smoke  # 真 API 冒烟：pytest -m smoke 单独跑，日常回归不花钱
def test_bailian_real_api_smoke():
    s = get_settings()
    assert s.dashscope_api_key, "backend/.env 缺 DASHSCOPE_API_KEY"
    emb = BailianEmbedder(api_key=s.dashscope_api_key, base_url=s.dashscope_compat_base,
                          model=s.embedding_model, dimensions=s.embedding_dim)
    v = emb.embed(["生鲜退货政策"])[0]
    assert len(v) == s.embedding_dim
