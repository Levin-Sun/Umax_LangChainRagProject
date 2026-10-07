# 重排 provider：百炼原生 text-rerank（非 OpenAI 兼容格式）。
#
# 为什么单独一个客户端：chat/embedding 走兼容端点（/chat/completions、/embeddings），
# 而重排只有原生端点 `/services/rerank/text-rerank/text-rerank`，入参/出参形状都不同
# （input.query + input.documents → output.results[].index/relevance_score）。
# 兼容端点没有 /rerank（实测返回空），所以不能复用 ChatClient 那套。
import time
from collections.abc import Callable, Sequence

import httpx

RETRY_BACKOFF = [2, 5, 10]          # 与 ChatClient/BailianEmbedder 同一口径
RERANK_PATH = "/services/rerank/text-rerank/text-rerank"


class RerankError(RuntimeError):
    pass


def reorder(hits: list[dict], results: list[dict]) -> list[dict]:
    """把重排结果映射回候选，并挂上 rerank_score（未被打分的为 None）。

    **没被打分的候选排在后面但不丢**：丢候选等于悄悄降低召回，而召回下降是最难排查的一类
    "答案变差了"（问题出在检索，人却盯着提示词调）。网关直连与 .env 直连共用这一份映射，
    两条通路的顺序语义必须一模一样。
    """
    scored = [{**hits[r["index"]], "rerank_score": r["relevance_score"]} for r in results]
    seen = {r["index"] for r in results}
    return scored + [{**h, "rerank_score": None} for i, h in enumerate(hits) if i not in seen]


class RerankClient:
    def __init__(self, *, api_key: str, base_url: str, model: str,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 60):
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._sleep = sleep

    def rerank(self, query: str, documents: Sequence[str], top_n: int | None = None) -> dict:
        """→ {"results": [{"index", "relevance_score"}..., 降序], "prompt_tokens": n}

        空候选直接返回（不打接口：空请求既浪费钱、也可能被判非法参数）。
        """
        docs = list(documents)
        if not docs:
            return {"results": [], "prompt_tokens": 0}
        payload = {"model": self._model,
                   "input": {"query": query, "documents": docs},
                   # return_documents=False：正文我们本来就有，省带宽也省上游的序列化开销
                   "parameters": {"return_documents": False,
                                  **({"top_n": top_n} if top_n else {})}}
        last_err: Exception | None = None
        for wait in [0] + RETRY_BACKOFF:
            if wait:
                self._sleep(wait)
            try:
                resp = self._client.post(f"{self._base}{RERANK_PATH}",
                                         headers={"Authorization": f"Bearer {self._key}"},
                                         json=payload)
                resp.raise_for_status()
                return self._parse(resp.json(), len(docs))
            except Exception as e:
                last_err = e
        raise RerankError(f"重排调用失败（{self._model}）：{last_err}")

    @staticmethod
    def _parse(data: dict, n_docs: int) -> dict:
        """解析并**自己再排一次序**：不赌上游返回顺序（换了实现顺序就变，契约要握在自己手里）。

        越界/缺字段一律抛错而不是静默丢弃——上游契约坏了要现形，
        由调用方决定"降级为未重排顺序"，而不是这里悄悄给一个更差的顺序。
        """
        results = ((data or {}).get("output") or {}).get("results")
        if not isinstance(results, list) or not results:
            raise RerankError(f"重排响应格式异常：{str(data)[:200]}")
        parsed = []
        for r in results:
            idx, score = (r or {}).get("index"), (r or {}).get("relevance_score")
            if not isinstance(idx, int) or not 0 <= idx < n_docs or score is None:
                raise RerankError(f"重排响应含非法项：{str(r)[:120]}（候选 {n_docs} 篇）")
            parsed.append({"index": idx, "relevance_score": float(score)})
        parsed.sort(key=lambda r: -r["relevance_score"])
        usage = (data or {}).get("usage") or {}
        return {"results": parsed,
                "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)}
