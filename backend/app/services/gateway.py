# 模型网关（§3.2⑤）：所有 AI 调用走这一扇门——按 model_configs 表分场景路由、fallback 链、用量记账
# 决策：后台换模型=改表即生效（每次调用现读表，不加缓存）；明文 key 只在内存短暂存在
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.models import ModelConfig, UsageRecord
from app.services.chat import SYSTEM_PROMPT, ChatClient, build_user_prompt
from app.services.crypto import decrypt_secret

EMBED_BATCH = 10


class GatewayError(RuntimeError):
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
            raise GatewayError("没有已启用的 chat 模型配置（model_configs 表为空？）")
        for p in providers:  # fallback 链：本家失败（含 ChatClient 内部重试）切下一家
            client = ChatClient(api_key=p.api_key, base_url=p.base_url, model=p.model_name,
                                transport=self._transport, sleep=self._sleep, timeout=self._timeout)
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

    def make_chat_fn(self) -> Callable[[str, list[dict]], dict]:
        """适配 chat_fn 协议：网关内部不记账（log=False），台账统一由问答端点按登录者记一次。"""
        def chat_fn(query: str, hits: Sequence[dict]) -> dict:
            out = self.chat(
                [{"role": "system", "content": SYSTEM_PROMPT},
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
            raise GatewayError("没有已启用的 embedding 模型配置（model_configs 表为空？）")
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
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
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
