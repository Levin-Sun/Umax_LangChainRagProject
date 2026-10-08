# TDD 红灯：模型网关（§3.2⑤）——按 model_configs 表分场景路由、fallback 链、用量记账
# 决策：远程 API 一律 MockTransport；真调用只留冒烟
import json
from itertools import count

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import KnowledgeBase, ModelConfig, UsageRecord
from app.services.crypto import encrypt_secret
from app.services.gateway import GatewayError, ModelGateway, NoProviderError

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


# ---- 运行时换模型即生效（§C「改完即生效，不用改代码重启」）：装配组合函数 ----
# 网关每次调用现读表，但旧装配在启动时判定表空就不再接线——后台登记第一个模型要重启。
# 组合语义：NoProviderError（表里没有启用模型）→ 回退 .env 百炼直连；
# 其他 GatewayError（配了但全挂）→ 原样上抛，由问答端点转未命中兜底。
def test_chat_fallback_composition_prefers_gateway(engine, db):
    from app.services.compose import with_gateway_fallback
    from app.services.gateway import GatewayError

    gw = vi_gw = lambda q, hits: {"answer": "来自网关", "prompt_tokens": 1, "completion_tokens": 1}
    direct = lambda q, hits: {"answer": "来自直连", "prompt_tokens": 1, "completion_tokens": 1}
    assert with_gateway_fallback(gw, direct)("q", [])["answer"] == "来自网关"


def test_chat_fallback_composition_falls_back_when_no_provider(engine, db):
    from app.services.compose import with_gateway_fallback
    from app.services.gateway import NoProviderError

    def gw(q, hits):
        raise NoProviderError("没有已启用的 chat 模型配置（model_configs 表为空？）")
    direct = lambda q, hits: {"answer": "来自直连", "prompt_tokens": 1, "completion_tokens": 1}
    assert with_gateway_fallback(gw, direct)("q", [])["answer"] == "来自直连"
    assert with_gateway_fallback(gw, None)("q", []) is None  # 无直连可退 → None（端点按未命中处理）


def test_chat_fallback_composition_reraises_provider_failure(engine, db):
    import pytest as _pytest
    from app.services.compose import with_gateway_fallback
    from app.services.gateway import GatewayError

    def gw(q, hits):
        raise GatewayError("全部 chat 模型均调用失败")
    direct = lambda q, hits: {"answer": "来自直连", "prompt_tokens": 1, "completion_tokens": 1}
    with _pytest.raises(GatewayError):
        with_gateway_fallback(gw, direct)("q", [])


def test_embedder_fallback_composition(engine, db):
    from app.services.compose import FallbackEmbedder
    from app.services.gateway import NoProviderError

    class Gw:
        def embed(self, texts):
            raise NoProviderError("没有已启用的 embedding 模型配置（model_configs 表为空？）")

    class Direct:
        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    # 空表且无直连 → [None]*n（ingest 语义：BM25-only 入库不失败）
    assert FallbackEmbedder(Gw(), None).embed(["a", "b"]) == [None, None]
    # 空表有直连 → 走直连
    assert FallbackEmbedder(Gw(), Direct()).embed(["a"]) == [[0.0] * 4]


# ---- 视觉场景（传图提问放开）：vision 走 vision 场景模型、prompt 含 data URL、失败 fallback ----
def _vision_handler(fail_models=()):
    def handler(request):
        body = json.loads(request.content)
        if body["model"] in fail_models:
            return httpx.Response(500, json={"error": "boom"})
        content = body["messages"][0]["content"]
        url = next(p["image_url"]["url"] for p in content if p["type"] == "image_url")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"[{body['model']}] 图述({url[:20]}…)"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20}})
    return handler


def test_vision_routes_vision_scenario_and_passes_data_url(engine, db):
    _seed(engine, "vision", "v-model")
    _seed(engine, "chat", "c-model")   # chat 模型不该被 vision 调用
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_vision_handler()),
                      sleep=lambda s: None)
    out = gw.vision("data:image/png;base64,AAAA")
    assert out["caption"].startswith("[v-model] 图述(data:image/png;base6")
    assert out["prompt_tokens"] == 50 and out["model"] == "v-model"


def test_vision_falls_back_to_next_provider(engine, db):
    _seed(engine, "vision", "v-bad", rank=0)
    _seed(engine, "vision", "v-good", rank=1)
    gw = ModelGateway(engine, secret=SECRET,
                      transport=httpx.MockTransport(_vision_handler(fail_models={"v-bad"})),
                      sleep=lambda s: None)
    assert gw.vision("data:image/png;base64,AAAA")["model"] == "v-good"


def test_vision_without_provider_raises_no_provider(engine, db):
    from app.services.gateway import NoProviderError
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_vision_handler()),
                      sleep=lambda s: None)
    with pytest.raises(NoProviderError):
        gw.vision("data:image/png;base64,AAAA")



def _seed_kb(engine, kb_id):
    """usage_records.kb_id 是有 FK 的：台账要记"哪个库花的钱"，就得先真有那个库。"""
    with Session(engine) as s:
        s.add(KnowledgeBase(id=kb_id, tenant_id="default", name=f"库{kb_id}"))
        s.commit()

# ---- rerank 场景（§3.2 精排）：与 chat/embedding 同款的路由/降级/记账，但走原生端点 ----
def _rerank_handler(fail_models=()):
    def handler(request):
        body = json.loads(request.content)
        if body["model"] in fail_models:
            return httpx.Response(500, json={"error": "upstream boom"})
        docs = body["input"]["documents"]
        # 模拟真实重排语义：与问题相关的文档得分高（"生鲜"那篇）
        return httpx.Response(200, json={
            "output": {"results": [{"index": i, "relevance_score": 0.9 if "生鲜" in doc else 0.1}
                                   for i, doc in enumerate(docs)]},
            "usage": {"prompt_tokens": 42, "total_tokens": 42}})
    return handler


def test_rerank_reorders_candidates_and_logs_usage(engine, db):
    _seed(engine, "rerank", "r-primary", base_url="http://api.test/api/v1")
    _seed_kb(engine, 3)
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_rerank_handler()),
                      sleep=lambda s: None)
    cands = [{"id": 1, "kb_id": 3, "content": "打包胶带要多宽", "doc_name": "a"},
             {"id": 2, "kb_id": 3, "content": "生鲜不支持七天无理由退货", "doc_name": "b"}]
    out = gw.rerank("生鲜能退吗", cands, kb_id=3)
    assert [h["id"] for h in out] == [2, 1]            # 相关的排到最前
    assert out[0]["rerank_score"] == 0.9 and out[1]["rerank_score"] == 0.1
    rows = _usage_rows(engine, "rerank")               # 重排也要进台账（它是花钱的一步）
    assert len(rows) == 1 and rows[0].model == "r-primary" and rows[0].prompt_tokens == 42
    assert rows[0].kb_id == 3


def test_rerank_falls_back_to_next_provider(engine, db):
    _seed(engine, "rerank", "r-broken", rank=0)
    _seed(engine, "rerank", "r-rescue", rank=1)
    gw = ModelGateway(engine, secret=SECRET,
                      transport=httpx.MockTransport(_rerank_handler(fail_models={"r-broken"})),
                      sleep=lambda s: None)
    out = gw.rerank("生鲜能退吗", [{"id": 1, "content": "无关"}], log=False)
    assert len(out) == 1
    assert _usage_rows(engine, "rerank") == []          # log=False：只返回不记账（调用方自己记）


def test_rerank_distinguishes_no_provider_from_all_dead(engine, db):
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_rerank_handler()),
                      sleep=lambda s: None)
    with pytest.raises(NoProviderError):               # 表里没有 → 调用方据此回退 .env 直连
        gw.rerank("q", [{"id": 1, "content": "x"}])
    _seed(engine, "rerank", "r-dead")
    gw2 = ModelGateway(engine, secret=SECRET,
                       transport=httpx.MockTransport(_rerank_handler(fail_models={"r-dead"})),
                       sleep=lambda s: None)
    with pytest.raises(GatewayError):                  # 配了但全挂 → 不悄悄降级，让配置错误现形
        gw2.rerank("q", [{"id": 1, "content": "x"}])


def test_rerank_keeps_unscored_candidates_and_carries_kb_id(engine, db):
    """上游只给部分候选打分时，没打分的排在后面但**不能丢**（丢候选=悄悄降低召回）。"""
    _seed(engine, "rerank", "r-partial")
    _seed_kb(engine, 9)
    def partial(request):
        return httpx.Response(200, json={"output": {"results": [
            {"index": 1, "relevance_score": 0.5}]}, "usage": {"total_tokens": 5}})
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(partial),
                      sleep=lambda s: None)
    hits = [{"id": 1, "kb_id": 9, "content": "a"}, {"id": 2, "kb_id": 9, "content": "b"}]
    out = gw.make_rerank_fn()("q", hits)
    assert [h["id"] for h in out] == [2, 1]
    assert out[0]["rerank_score"] == 0.5 and out[1]["rerank_score"] is None
    # make_rerank_fn 自己记账：检索层没有"提问者"这个上下文，成本按链路记（同 embedding 口径）
    assert [r.kb_id for r in _usage_rows(engine, "rerank")] == [9]


# ---- HTTP 客户端复用（评审发现：每次调用新建客户端 = 每次重新 TLS 握手，实测 +193ms/次）----
def _counting(monkeypatch, attr_name):
    """把 gateway 模块里的客户端类换成记账子类：能数出"到底建了几个客户端"。"""
    import app.services.gateway as gw_mod

    created = []
    real = getattr(gw_mod, attr_name)

    class Counting(real):      # type: ignore[misc, valid-type]
        def __init__(self, **kw):
            super().__init__(**kw)
            created.append(kw)

    monkeypatch.setattr(gw_mod, attr_name, Counting)
    return created


def test_gateway_reuses_one_client_per_provider(engine, db, monkeypatch):
    created = _counting(monkeypatch, "ChatClient")
    _seed(engine, "chat", "m1")
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_chat_handler()),
                      sleep=lambda s: None)
    for _ in range(3):
        gw.chat([{"role": "user", "content": "hi"}])
    assert len(created) == 1, f"3 次调用建了 {len(created)} 个 HTTP 客户端 → 连接池无法复用"


def test_client_cache_follows_config_changes(engine, db, monkeypatch):
    """缓存不能破坏"改表即生效"：改了 base_url / 模型名 / key 必须换新客户端。"""
    created = _counting(monkeypatch, "ChatClient")
    _seed(engine, "chat", "m1")
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_chat_handler()),
                      sleep=lambda s: None)
    gw.chat([{"role": "user", "content": "hi"}])
    assert len(created) == 1

    with Session(engine) as s:          # 轮换 key（后台"改表即生效"的典型动作）
        row = s.query(ModelConfig).filter_by(scenario="chat").one()
        row.encrypted_api_key = encrypt_secret("sk-rotated", SECRET)
        s.commit()
    gw.chat([{"role": "user", "content": "hi"}])
    assert len(created) == 2, "轮换 key 后仍在用旧客户端 → 新 key 不生效"

    with Session(engine) as s:          # 只改 fallback_rank（与连接无关）：不该换客户端
        row = s.query(ModelConfig).filter_by(scenario="chat").one()
        row.fallback_rank = 1
        s.commit()
    gw.chat([{"role": "user", "content": "hi"}])
    assert len(created) == 2, "改了与连接无关的字段却重建了客户端"


def test_client_cache_is_bounded(engine, db, monkeypatch):
    """管理员反复轮换 key 不该让旧客户端无限堆积（每个都握着一个连接池）。"""
    from app.services.gateway import CLIENT_CACHE_MAX

    created = _counting(monkeypatch, "ChatClient")
    _seed(engine, "chat", "m1")
    gw = ModelGateway(engine, secret=SECRET, transport=httpx.MockTransport(_chat_handler()),
                      sleep=lambda s: None)
    for i in range(CLIENT_CACHE_MAX + 3):
        with Session(engine) as s:
            row = s.query(ModelConfig).filter_by(scenario="chat").one()
            row.encrypted_api_key = encrypt_secret(f"sk-{i}", SECRET)
            s.commit()
        gw.chat([{"role": "user", "content": "hi"}])
    assert len(gw._clients) <= CLIENT_CACHE_MAX
    assert len(created) > CLIENT_CACHE_MAX      # 确实经历了淘汰重建，而不是一直命中同一个


def test_embedding_and_rerank_also_reuse_clients(engine, db, monkeypatch):
    """三条模型通路（chat/embedding/rerank）都要复用——只修一条等于只省 1/3 的手握时间。"""
    http_created = _counting(monkeypatch, "RerankClient")
    _seed(engine, "embedding", "e1")
    _seed(engine, "rerank", "r1")
    gw = ModelGateway(engine, secret=SECRET,
                      transport=httpx.MockTransport(lambda req: httpx.Response(
                          200, json=({"data": [{"index": 0, "embedding": [1.0]}],
                                      "usage": {"prompt_tokens": 1}}
                                     if "embeddings" in str(req.url) else
                                     {"output": {"results": [{"index": 0,
                                                              "relevance_score": 0.5}]},
                                      "usage": {"total_tokens": 1}}))),
                      sleep=lambda s: None)
    gw.embed(["a"]); gw.embed(["b"])
    gw.rerank("q", [{"id": 1, "content": "x"}]); gw.rerank("q", [{"id": 1, "content": "x"}])
    assert len(http_created) == 1, f"rerank 建了 {len(http_created)} 个客户端"
