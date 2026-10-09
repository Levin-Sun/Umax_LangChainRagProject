# 内置厂商标本 + 一键配齐。
#
# 真机教训（2026-10-09/10，连着四个坑）：地址少了 /v1、厂商只有对话没有向量、模型名写错、
# 向量维度不匹配——全是"让客户自己填厂商实现细节"造成的。标本就是那份本该由产品内置的知识，
# 所以它必须：形状可自检、覆盖"对话+向量"这类刚需、并且**永远留着自建/手填入口**。
from app.api.schemas import SCENARIOS
from app.services.catalog import catalog_problems, load_catalog

CATALOG = "/api/v1/model-catalog"
BUNDLE = "/api/v1/model-bundles"


def _client(engine, tmp_path, **kw):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import login, seed_user

    app = create_app(engine=engine, secret="k", upload_dir=str(tmp_path),
                     embedder=kw.get("embedder"), chat_fn=kw.get("chat_fn"),
                     vision_fn=kw.get("vision_fn"), rerank_fn=kw.get("rerank_fn"))
    seed_user(engine, "admin@x.com", "Adm1n-Pass-123", role="admin")
    c = TestClient(app)
    login(c, "admin@x.com", "Adm1n-Pass-123")
    return c


def test_catalog_is_well_formed():
    """标本自检：形状不对要在 CI 先红，而不是等客户点开下拉才发现。"""
    assert catalog_problems() == []


def test_catalog_covers_the_essentials_and_keeps_a_manual_door():
    cat = load_catalog()
    ids = {v["id"] for v in cat["vendors"]}
    assert {"bailian", "stepfun", "deepseek", "self"} <= ids
    # 至少要有一家能同时提供对话与向量：否则"一键配齐"每次都要客户再补一家
    assert [v["id"] for v in cat["vendors"]
            if {"chat", "embedding"} <= {c["key"] for c in v["capabilities"]}]
    # 自建/本地部署必须留着手填入口（内网、代理网关这些长尾一定存在）
    assert next(v for v in cat["vendors"] if v["id"] == "self")["capabilities"] == []
    # 拼音与别名（客户会怎么打字：de / 百炼 / abl / jyx）
    bailian = next(v for v in cat["vendors"] if v["id"] == "bailian")
    assert "百炼" in bailian["aliases"] and "abl" in bailian["pinyin"]


def test_catalog_endpoint_exposes_vendors_with_scenarios(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    r = c.get(CATALOG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["catalog_version"] and body["capability_labels"]["embedding"]
    assert body["capability_miss"]["embedding"]            # "没配会怎样"的文案由后端给
    for v in body["vendors"]:
        pids = {p["id"] for p in v["profiles"]}
        for cap in v["capabilities"]:
            assert cap["scenario"] in SCENARIOS
            assert cap["profile"] in pids


def test_bundle_registers_one_row_per_capability_with_right_endpoint(engine, db, tmp_path):
    """一键配齐：接入方式由能力决定（精排走原生端点、其它走兼容模式），客户不选地址。"""
    c = _client(engine, tmp_path)
    r = c.post(BUNDLE, json={"vendor_id": "bailian", "api_key": "sk-test", "test": False})
    assert r.status_code == 201, r.text
    items = {i["capability"]: i for i in r.json()["items"]}
    assert set(items) == {"chat", "embedding", "vision", "rerank"}
    assert all(i["action"] == "created" for i in items.values())
    assert items["chat"]["base_url"].endswith("/compatible-mode/v1")
    assert items["rerank"]["base_url"].endswith("/api/v1")       # 精排必须走原生端点
    rows = {m["scenario"]: m for m in c.get("/api/v1/models").json()}
    assert set(rows) == {"chat", "embedding", "vision", "rerank"}
    assert rows["embedding"]["model_name"] == "text-embedding-v4"
    assert rows["embedding"]["capabilities"]["dim"] == 1024      # 能力标签不再空着


def test_bundle_is_idempotent_and_rotates_the_key(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    first = c.post(BUNDLE, json={"vendor_id": "bailian", "api_key": "sk-a", "test": False}).json()
    assert all(i["action"] == "created" for i in first["items"])
    second = c.post(BUNDLE, json={"vendor_id": "bailian", "api_key": "sk-b", "test": False}).json()
    assert all(i["action"] == "updated" for i in second["items"])
    # 反复点不会堆出重复配置——客户换 key 重新配也只有这一条路径
    assert len(c.get("/api/v1/models").json()) == len(first["items"])


def test_bundle_rejects_unknown_vendor_and_missing_capability(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    assert c.post(BUNDLE, json={"vendor_id": "nope", "api_key": "k"}).status_code == 400
    # 只有对话的厂商被要求配向量 → 400，且指路手动登记（长尾出路不能没有）
    r = c.post(BUNDLE, json={"vendor_id": "deepseek", "api_key": "k",
                             "capabilities": ["embedding"], "test": False})
    assert r.status_code == 400 and "手动登记" in r.text
    assert c.post(BUNDLE, json={"vendor_id": "self", "api_key": "k"}).status_code == 400


def test_bundle_only_touches_picked_capabilities_and_reports_probe_error(engine, db, tmp_path):
    """登记即试调：厂商的原始报错要回给界面（标本会过期，错误必须当场可见）。"""
    def broken(query, hits):
        raise RuntimeError("模型名不存在")

    c = _client(engine, tmp_path, chat_fn=broken)
    r = c.post(BUNDLE, json={"vendor_id": "bailian", "api_key": "k", "capabilities": ["chat"]})
    assert r.status_code == 201, r.text
    item = r.json()["items"][0]
    assert item["capability"] == "chat" and item["ok"] is False
    assert "模型名不存在" in item["detail"]
    assert [m["scenario"] for m in c.get("/api/v1/models").json()] == ["chat"]


def test_bundle_blocks_embedding_dim_mismatch(engine, db, tmp_path, monkeypatch):
    """维度必须等于库里的 VECTOR(n)：不等就当场拦下，而不是等写库那一步炸。"""
    import copy

    bad = copy.deepcopy(load_catalog())
    for v in bad["vendors"]:
        if v["id"] == "bailian":
            for cap in v["capabilities"]:
                if cap["key"] == "embedding":
                    cap["dim"] = 1536
    monkeypatch.setattr("app.api.routers.models_admin.load_catalog", lambda: bad)
    c = _client(engine, tmp_path)
    r = c.post(BUNDLE, json={"vendor_id": "bailian", "api_key": "k",
                             "capabilities": ["embedding"], "test": False})
    assert r.status_code == 400 and "VECTOR(1024)" in r.text
