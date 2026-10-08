# 模型通路的组合器：把"网关优先、.env 直连兜底"这类策略收在一处。
#
# 为什么单独一个模块：这些组合器**只关心调用语义**（谁先谁后、失败算不算失败），
# 与 FastAPI 装配无关；而且它们的语义差别极微妙——chat 在"无供应商"时返回 None（含义是
# "没有答案"），精排返回 None 却会把整条检索打断。两种语义放在一起看，才不容易被复用错
# （真机踩过：精排误用 chat 的组合器，/retrieve 直接 500）。
from collections.abc import Callable


def with_gateway_fallback(gw_fn: Callable | None,
                          direct_fn: Callable | None) -> Callable | None:
    """运行时换模型即生效（§C）的组合装配：网关 fn 每次调用现读 model_configs，
    表里还没有启用模型（NoProviderError）时回退 .env 直连；两侧都没有 → None
    （问答端点按未命中兜底）。配了但全挂的 GatewayError 原样上抛，由端点转兜底。"""
    from app.services.gateway import GatewayError, NoProviderError

    if gw_fn is None:
        return direct_fn
    if direct_fn is None:
        def gw_only(query, hits):
            try:
                return gw_fn(query, hits)
            except NoProviderError:
                return None   # 无直连可退：None 由问答端点按未命中兜底处理
        return gw_only

    def composed(query, hits):
        try:
            return gw_fn(query, hits)
        except NoProviderError:
            return direct_fn(query, hits)

    return composed


def with_rerank_fallback(gw_fn: Callable | None, direct_fn: Callable | None) -> Callable | None:
    """组合两路精排（网关优先、.env 原生端点兜底）。

    与 chat 的 `with_gateway_fallback` 形状相似但**语义必须不同**：chat 在"无供应商"时返回 None，
    含义是"没有答案"；而精排**返回 None 会把检索整条打断**（retrieve 会拿 None 去切片）。
    真机踩过：复用 chat 那个组合器后，网关表里没登记 rerank 时 /retrieve 直接 500。
    所以精排的"NoProviderError"语义是"这一路不可用"，交给另一路或原样返回候选。
    """
    from app.services.gateway import NoProviderError

    if gw_fn is None:
        return direct_fn
    if direct_fn is None:
        return gw_fn

    def composed(query: str, hits: list[dict]) -> list[dict]:
        try:
            return gw_fn(query, hits)
        except NoProviderError:
            return direct_fn(query, hits)

    return composed


def with_rerank_degrade(rerank_fn: Callable | None) -> Callable | None:
    """重排失败降级为**未重排的顺序**，只记 warning。

    与 chat 的口径刻意不同：chat 挂了就没有答案，必须上抛让端点走未命中兜底；
    而重排只是检索链路里"锦上添花"的一步，它挂了还答得出来——为此把整个问答弄失败不合理。
    这类失败（上游抖动/模型被停用）不影响数据正确性，记 warning 足够，不该惊动用户。
    """
    import logging

    if rerank_fn is None:
        return None

    def wrapped(query: str, hits: list[dict]) -> list[dict]:
        try:
            return rerank_fn(query, hits)
        except Exception as exc:
            logging.getLogger("umax").warning("重排失败，改用未重排顺序：%s", exc)
            return hits

    return wrapped


class FallbackEmbedder:
    """组合 embedder（网关优先、.env 百炼兜底）：网关表空（NoProviderError）退直连；
    两侧都无 → 返回 [None]*n——ingest 语义即"不向量化 BM25-only 入库"，
    后台登记 embedding 模型后点重处理即可补向量，无需重启。"""

    def __init__(self, gw_embedder, direct_embedder) -> None:
        self._gw, self._direct = gw_embedder, direct_embedder

    def embed(self, texts):
        from app.services.gateway import NoProviderError

        try:
            return self._gw.embed(texts)
        except NoProviderError:
            if self._direct is None:
                return [None] * len(texts)
            return self._direct.embed(texts)
