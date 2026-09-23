# TDD 红灯：模型网关（§3.2⑤）——按 model_configs 表分场景路由、fallback 链、用量记账
# 决策：远程 API 一律 MockTransport；真调用只留冒烟
import json
from itertools import count

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import ModelConfig, UsageRecord
from app.services.crypto import encrypt_secret
from app.services.gateway import GatewayError, ModelGateway

SECRET = "gateway-master"


def _seed(engine, scenario, model, base_url="http://api.test/v1", *, rank=0,
          enabled=True, key="sk-live-key", caps=None):
    with Session(engine) as s:
        s.add(ModelConfig(scenario=scenario, provider="generic", base_url=base_url,
                          encrypted_api_key=encrypt_secret(key, SECRET),
                          model_name=model, capabilities=caps or {},
                          fallback_rank=rank, enabled=enabled))
        s.commit()


def _chat_handler(fail_models=()):
    def handler(request):
        body = json.loads(request.content)
        model = body["model"]
        if model in fail_models:
            return httpx.Response(500, json={"error": "upstream boom"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"[{model}] 据资料回答"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7}})
    return handler


def _embed_handler(fail_models=()):
    seq = count(1)
    def handler(request):
        body = json.loads(request.content)
        if body["model"] in fail_models:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={
            "data": [{"index": i, "embedding": [float(n)] * 4} for i, n in zip(range(len(body["input"])), seq)],
            "usage": {"prompt_tokens": 6, "total_tokens": 6}})
    return handler


@pytest.fixture
def gateway(engine, db):
    """db 仅用于清表；网关自开 Session。"""
    return ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_chat_handler()),
                        sleep=lambda s: None)


def _usage_rows(engine, scenario):
    with Session(engine) as s:
        return s.query(UsageRecord).filter_by(scenario=scenario).all()


# ---- 路由：读表、按 (fallback_rank, id) 排序、enabled/场景过滤、key 解密 ----
def test_providers_ordered_by_rank_then_id_and_filtered(engine, db, gateway):
    _seed(engine, "chat", "m-second", rank=1)
    _seed(engine, "chat", "m-first", rank=0)
    _seed(engine, "chat", "m-off", rank=0, enabled=False)
    _seed(engine, "embedding", "m-embed", rank=0)
    provs = gateway.providers("chat")
    assert [p.model_name for p in provs] == ["m-first", "m-second"]
    assert provs[0].api_key == "sk-live-key"  # 出库即解密，明文只在内存


def test_no_providers_raises(engine, db, gateway):
    with pytest.raises(GatewayError, match="chat"):
        gateway.chat([{"role": "user", "content": "hi"}])


# ---- chat：走主用模型、记一笔台账（真实模型名+延迟） ----
def test_chat_uses_primary_and_logs_usage(engine, db, gateway):
    from app.models import KnowledgeBase
    kb = KnowledgeBase(name="k")
    db.add(kb)
    db.commit()
    _seed(engine, "chat", "m-main")
    out = gateway.chat([{"role": "user", "content": "退货政策？"}], user_email="a@x.com", kb_id=kb.id)
    assert out["text"] == "[m-main] 据资料回答"
    assert out["model"] == "m-main"
    assert (out["prompt_tokens"], out["completion_tokens"]) == (11, 7)
    assert out["latency_ms"] >= 0
    rows = _usage_rows(engine, "chat")
    assert len(rows) == 1
    assert (rows[0].user_email, rows[0].kb_id, rows[0].model) == ("a@x.com", kb.id, "m-main")
    assert rows[0].completion_tokens == 7


# ---- fallback：主用 500 → 自动切备用，台账记实际用成的模型 ----
def test_chat_falls_back_when_primary_fails(engine, db):
    _seed(engine, "chat", "m-broken", rank=0)
    _seed(engine, "chat", "m-rescue", rank=1)
    gw = ModelGateway(engine, secret=SECRET,
                      transport=httpx.MockTransport(_chat_handler(fail_models={"m-broken"})),
                      sleep=lambda s: None)
    out = gw.chat([{"role": "user", "content": "hi"}])
    assert out["model"] == "m-rescue"
    rows = _usage_rows(engine, "chat")
    assert len(rows) == 1 and rows[0].model == "m-rescue"


def test_chat_raises_when_all_providers_fail(engine, db):
    _seed(engine, "chat", "m-b1", rank=0)
    _seed(engine, "chat", "m-b2", rank=1)
    gw = ModelGateway(engine, secret=SECRET,
                      transport=httpx.MockTransport(_chat_handler(fail_models={"m-b1", "m-b2"})),
                      sleep=lambda s: None)
    with pytest.raises(GatewayError):
        gw.chat([{"role": "user", "content": "hi"}])
    assert _usage_rows(engine, "chat") == []  # 全挂不记账


# ---- 记账两轨：chat(log=True) 直连归网关记，chat(log=False)/make_chat_fn 不记 ----
def test_chat_log_false_returns_without_usage_row(engine, db, gateway):
    """端点自己按登录者记账，网关这条路径必须零落账（旧 logged 约定的替代物）。"""
    _seed(engine, "chat", "m-main")
    out = gateway.chat([{"role": "user", "content": "hi"}], log=False)
    assert out["text"] == "[m-main] 据资料回答"
    assert out["model"] == "m-main" and out["latency_ms"] >= 0
    assert _usage_rows(engine, "chat") == [], "log=False 不落账"


def test_make_chat_fn_protocol_never_logs(engine, db, gateway):
    """适配 chat_fn 协议：结果字段齐备、无 logged 键（约定已退役）、网关不记账。"""
    _seed(engine, "chat", "m-main")
    fn = gateway.make_chat_fn()
    out = fn("生鲜怎么退？", [{"doc_name": "手册.txt", "content": "不支持七天无理由"}])
    assert out["answer"] == "[m-main] 据资料回答"
    assert out["model"] == "m-main"
    assert "logged" not in out
    assert _usage_rows(engine, "chat") == []


# ---- embedding：按 embedding 场景路由、按 index 还原顺序、记台账 ----
def test_embed_routes_by_scenario_and_logs(engine, db):
    _seed(engine, "embedding", "emb-main", rank=0, caps={"dimensions": 4})
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_embed_handler()),
                      sleep=lambda s: None)
    vecs = gw.embed(["甲", "乙"])
    assert [v[0] for v in vecs] == [1.0, 2.0]  # index 顺序还原
    rows = _usage_rows(engine, "embedding")
    assert len(rows) == 1 and rows[0].model == "emb-main"


def test_embed_falls_back_and_makes_embedder_adapter(engine, db):
    _seed(engine, "embedding", "emb-broken", rank=0, caps={"dimensions": 4})
    _seed(engine, "embedding", "emb-rescue", rank=1, caps={"dimensions": 4})
    gw = ModelGateway(engine, secret=SECRET,
                      transport=httpx.MockTransport(_embed_handler(fail_models={"emb-broken"})),
                      sleep=lambda s: None)
    embedder = gw.make_embedder()  # 暴露 .embed(texts)，直接兼容 ingest/retrieval 注入点
    assert embedder.embed(["x"])[0][0] == 1.0  # 失败的 429 不消耗序号，成功首个仍是 1.0
    assert _usage_rows(engine, "embedding")[0].model == "emb-rescue"
