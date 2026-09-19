# 百炼嵌入 provider（OpenAI 兼容 /embeddings，批 10 + 退避重试，迁自 stage0）
import time
from collections.abc import Sequence
from typing import Callable

import httpx

RETRY_BACKOFF = [2, 5, 10]
BATCH = 10


class BailianEmbedder:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int = 1024,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60,
    ):
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._dim = dimensions
        self._sleep = sleep

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        items = list(texts)
        for i in range(0, len(items), BATCH):
            out.extend(self._embed_batch(items[i : i + BATCH]))
        return out

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_err: Exception | None = None
        for attempt, wait in enumerate([0] + RETRY_BACKOFF):
            if wait:
                self._sleep(wait)
            try:
                resp = self._client.post(
                    f"{self._base}/embeddings",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={"model": self._model, "input": batch,
                          "dimensions": self._dim, "encoding_format": "float"},
                )
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
            except Exception as e:
                last_err = e
        raise last_err  # type: ignore[misc]
