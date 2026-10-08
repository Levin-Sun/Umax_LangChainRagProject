# 模型网关（§3.2⑤）：所有 AI 调用走这一扇门——按 model_configs 表分场景路由、fallback 链、用量记账
# 决策：后台换模型=改表即生效（每次调用现读表，不加缓存）；明文 key 只在内存短暂存在
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.models import ModelConfig, UsageRecord
from app.services.chat import ChatClient, build_user_prompt
from app.services.crypto import decrypt_secret
from app.services.rerank import RerankClient, reorder

EMBED_BATCH = 10
CLIENT_CACHE_MAX = 32   # HTTP 客户端缓存上限：管理员反复轮换 key 时不至于让旧连接池无限堆积


class GatewayError(RuntimeError):
    pass


class NoProviderError(GatewayError):
    """表里没有已启用的该场景模型——装配组合函数借此判断"网关未配置"，回退 .env 直连；
    与"配了但全挂"（裸 GatewayError）区分：后者上抛，由问答端点转未命中兜底。"""
    pass


@dataclass
class Provider:
    id: int
    provider: str
    base_url: str
    model_name: str
    api_key: str
    capabilities: dict


class ModelGateway:
    def __init__(self, engine, *, secret: str,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 120):
        self._engine = engine
        self._secret = secret
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout
        self._clients: dict[tuple, object] = {}

    def _client(self, key: tuple, factory: Callable[[], object]):
        """按 provider 配置缓存 HTTP 客户端——**连接池复用**是本方法的全部意义。

        每次调用新建 httpx.Client 意味着空连接池 → 每次重新做 TLS 握手。实测（真百炼端点，
        同一问题连发 8 次）：新建 449ms/次 vs 复用 256ms/次，**每次调用白付约 193ms**；
        配了精排后一问一答两次调用，白付近 400ms。私有化交付里延迟是客户的第一感受。

        缓存键含 base_url/模型名/**解密后的 key**：后台改这三项立即换新客户端
        （"改表即生效"不被缓存破坏）；只改 fallback_rank/enabled 这类与连接无关的字段则沿用。
        上限 CLIENT_CACHE_MAX，超了整批清空——粗暴但够用，且下一批调用各建一次后立刻恢复复用。
        """
        got = self._clients.get(key)
        if got is None:
            if len(self._clients) >= CLIENT_CACHE_MAX:
                self._clients.clear()
            got = self._clients[key] = factory()
        return got

    def _http(self, p: Provider) -> httpx.Client:
        """共享的底层 HTTP 客户端（embedding 用，走原样 httpx）。"""
        return self._client(("http", p.base_url, p.model_name, p.api_key),
                            lambda: httpx.Client(transport=self._transport,
                                                 timeout=self._timeout))

    def _chat_client(self, p: Provider) -> ChatClient:
        return self._client(("chat", p.base_url, p.model_name, p.api_key),
                            lambda: ChatClient(api_key=p.api_key, base_url=p.base_url,
                                               model=p.model_name, transport=self._transport,
                                               sleep=self._sleep, timeout=self._timeout))

    def _rerank_client(self, p: Provider) -> RerankClient:
        return self._client(("rerank", p.base_url, p.model_name, p.api_key),
                            lambda: RerankClient(api_key=p.api_key, base_url=p.base_url,
                                                 model=p.model_name, transport=self._transport,
                                                 sleep=self._sleep, timeout=self._timeout))

    def providers(self, scenario: str) -> list[Provider]:
        with Session(self._engine) as s:
            rows = (s.query(ModelConfig)
                    .filter_by(scenario=scenario, enabled=True)
                    .order_by(ModelConfig.fallback_rank, ModelConfig.id).all())
        return [Provider(r.id, r.provider, r.base_url, r.model_name,
                         decrypt_secret(r.encrypted_api_key, self._secret), r.capabilities or {})
                for r in rows]

    def _log(self, user_email: str, kb_id: int | None, scenario: str, model: str,
             prompt_tokens: int, completion_tokens: int, latency_ms: int) -> None:
        with Session(self._engine) as s:
            s.add(UsageRecord(user_email=user_email, kb_id=kb_id, scenario=scenario,
                              model=model, prompt_tokens=prompt_tokens,
                              completion_tokens=completion_tokens, latency_ms=latency_ms))
            s.commit()

    def chat(self, messages: list[dict], *, user_email: str = "system@local",
             kb_id: int | None = None, log: bool = True) -> dict:
        """log=True（默认，直连调用方）由网关记一笔台账；log=False 只返回结果不落账——
        问答端点自己按"真实登录者邮箱"记账，避免同一请求双记（logged 约定已退役）。"""
        providers = self.providers("chat")
        if not providers:
            raise NoProviderError("没有已启用的 chat 模型配置（model_configs 表为空？）")
        for p in providers:  # fallback 链：本家失败（含 ChatClient 内部重试）切下一家
            client = self._chat_client(p)
            t0 = time.monotonic()
            try:
                out = client.complete(messages)
            except Exception:
                continue
            out["model"] = p.model_name
            out["latency_ms"] = int((time.monotonic() - t0) * 1000)
            if log:
                self._log(user_email, kb_id, "chat", p.model_name, out["prompt_tokens"],
                          out["completion_tokens"], out["latency_ms"])
            return out
        raise GatewayError("全部 chat 模型均调用失败")

    def vision(self, image_data_url: str, *, prompt=None) -> dict:
        """视觉场景（传图提问）：vision 模型把图转文字描述。记账交回端点（同 chat 口径）。
        prompt 可传字符串/零参回调（后台可改的识图提示词）。"""
        from app.services.chat import VISION_PROMPT, resolve_prompt as _rp

        vision_prompt = _rp(prompt) if prompt else VISION_PROMPT
        providers = self.providers("vision")
        if not providers:
            raise NoProviderError("没有已启用的 vision 模型配置（model_configs 表为空？）")
        for p in providers:  # fallback 链同 chat
            client = self._chat_client(p)
            messages = [{"role": "user", "content": [
                {"type": "text", "text": vision_prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}}]}]
            t0 = time.monotonic()
            try:
                out = client.complete(messages)
            except Exception:
                continue
            return {"caption": out["text"], "prompt_tokens": out["prompt_tokens"],
                    "completion_tokens": out["completion_tokens"], "model": p.model_name,
                    "latency_ms": int((time.monotonic() - t0) * 1000)}
        raise GatewayError("全部 vision 模型均调用失败")

    def make_vision_fn(self, *, vision_prompt=None) -> Callable[[str], dict]:
        return lambda image_url: self.vision(image_url, prompt=vision_prompt)

    def rerank(self, query: str, hits: list[dict], *, top_n: int | None = None,
               user_email: str = "system@local", kb_id: int | None = None,
               log: bool = True) -> list[dict]:
        """精排（§3.2 混合检索后的重排环节）：把候选块按与问题的相关性重排。

        记账口径与 embedding 相同（归 system@local / 记命中块所属库）：重排是**检索链路的一环**，
        与"谁提的问题"无关——硬要归到提问者头上只会让台账失真（检索链路的成本按链路记）。
        返回的每项带 rerank_score（未被打分的为 None），排查"为什么这条被排到后面"时是唯一线索。
        """
        providers = self.providers("rerank")
        if not providers:
            raise NoProviderError("没有已启用的 rerank 模型配置（model_configs 表为空？）")
        contents = [h.get("content", "") for h in hits]
        for p in providers:   # fallback 链：本家失败切下一家
            client = self._rerank_client(p)
            t0 = time.monotonic()
            try:
                out = client.rerank(query, contents, top_n=top_n or len(contents))
            except Exception:
                continue
            if log:
                self._log(user_email, kb_id, "rerank", p.model_name, out["prompt_tokens"], 0,
                          int((time.monotonic() - t0) * 1000))
            return reorder(hits, out["results"])
        raise GatewayError("全部 rerank 模型均调用失败")

    def make_rerank_fn(self, *, top_n: int | None = None) -> Callable[[str, list[dict]], list[dict]]:
        """适配 retrieval.rerank 协议：(query, 候选) -> 重排后的候选。

        与 chat 的记账约定刻意不同：chat 交给问答端点按登录者记（那里知道是谁在问），
        而检索层没有"提问者"这个上下文——所以这里自己记（归检索链路，同 embedding 口径）。
        """
        def rerank_fn(query: str, candidates: list[dict]) -> list[dict]:
            return self.rerank(query, candidates, top_n=top_n,
                               kb_id=(candidates[0].get("kb_id") if candidates else None))
        return rerank_fn

    def make_chat_fn(self, *, system_prompt=None) -> Callable[[str, list[dict]], dict]:
        """适配 chat_fn 协议：网关内部不记账（log=False），台账统一由问答端点按登录者记一次。
        system_prompt：None/字符串/零参回调——每次调用现取，后台改提示词即时生效。"""
        from app.services.chat import resolve_prompt

        def chat_fn(query: str, hits: Sequence[dict]) -> dict:
            out = self.chat(
                [{"role": "system", "content": resolve_prompt(system_prompt)},
                 {"role": "user", "content": build_user_prompt(query, hits)}],
                log=False, kb_id=(hits[0].get("kb_id") if hits else None))
            return {"answer": out["text"], "prompt_tokens": out["prompt_tokens"],
                    "completion_tokens": out["completion_tokens"], "model": out["model"],
                    "latency_ms": out["latency_ms"]}
        return chat_fn

    def embed(self, texts: list[str], *, user_email: str = "system@local",
              kb_id: int | None = None) -> list[list[float]]:
        providers = self.providers("embedding")
        if not providers:
            raise NoProviderError("没有已启用的 embedding 模型配置（model_configs 表为空？）")
        for p in providers:  # provider 级 fallback：整批失败切下一家
            try:
                vectors, prompt_tokens = self._embed_all(p, texts)
            except Exception:
                continue
            self._log(user_email, kb_id, "embedding", p.model_name, prompt_tokens, 0, 0)
            return vectors
        raise GatewayError("全部 embedding 模型均调用失败")

    def make_embedder(self, **kwargs) -> "_GatewayEmbedder":
        return _GatewayEmbedder(self, kwargs)

    def _embed_all(self, p: Provider, texts: list[str]) -> tuple[list[list[float]], int]:
        out: list[list[float]] = []
        prompt_tokens = 0
        client = self._http(p)      # 复用连接池（同 chat/rerank 的理由）
        for i in range(0, len(texts), EMBED_BATCH):
            resp = client.post(
                f"{p.base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {p.api_key}"},
                json={"model": p.model_name, "input": texts[i:i + EMBED_BATCH]})
            resp.raise_for_status()
            data = resp.json()
            out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
            prompt_tokens += (data.get("usage") or {}).get("prompt_tokens", 0)
        return out, prompt_tokens


class _GatewayEmbedder:
    """暴露 .embed(texts)，与 BailianEmbedder/FakeEmbedder 注入点完全兼容。"""

    def __init__(self, gateway: ModelGateway, kwargs: dict):
        self._gateway = gateway
        self._kwargs = kwargs

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._gateway.embed(texts, **self._kwargs)
