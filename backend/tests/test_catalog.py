# 内置厂商标本 + 一键配齐。
#
# 真机教训（2026-10-09/10，连着四个坑）：地址少了 /v1、厂商只有对话没有向量、模型名写错、
# 向量维度不匹配——全是"让客户自己填厂商实现细节"造成的。标本就是那份本该由产品内置的知识，
# 所以它必须：形状可自检、覆盖"对话+向量"这类刚需、并且**永远留着自建/手填入口**。
import json

import pytest

from app.api.schemas import SCENARIOS
from app.services.catalog import catalog_problems, load_catalog

CATALOG = "/api/v1/model-catalog"
BUNDLE = "/api/v1/model-bundles"


@pytest.fixture(autouse=True)
def _fresh_catalog_cache():
    """标本带进程内缓存：每个用例前后清一次，覆盖文件的 monkeypatch 不会串到别的用例。"""
    load_catalog.cache_clear()
    yield
    load_catalog.cache_clear()


def _vendor(vid: str, *, name: str, base: str, cap_key: str = "chat",
            model: str = "m", dim: int | None = None) -> dict:
    cap = {"key": cap_key, "scenario": cap_key, "model": model, "profile": "p"}
    if dim is not None:
        cap["dim"] = dim
    return {"id": vid, "name": name, "aliases": [], "pinyin": [], "verified": True,
            "verified_at": "2026-10-10",
            "profiles": [{"id": "p", "name": "按量计费", "base_url": base}],
            "capabilities": [cap]}


def _write_override(tmp_path, monkeypatch, payload) -> None:
    p = tmp_path / "model_catalog.local.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
                 encoding="utf-8")
    monkeypatch.setenv("MODEL_CATALOG_OVERRIDE", str(p))


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


def test_catalog_only_lists_vendors_that_can_be_autoconfigured():
    """标本里只放"能一键配齐"的厂商（用户反馈：把"自建/本地部署"这种没有统一地址的选项去掉，
    统一走「高级：手动登记」）。所以每家都必须有真实接入地址与至少一项能力。"""
    cat = load_catalog()
    ids = {v["id"] for v in cat["vendors"]}
    assert {"bailian", "stepfun", "deepseek"} <= ids
    assert "self" not in ids
    # 至少要有一家能同时提供对话与向量：否则"一键配齐"每次都要客户再补一家
    assert [v["id"] for v in cat["vendors"]
            if {"chat", "embedding"} <= {c["key"] for c in v["capabilities"]}]
    for v in cat["vendors"]:
        assert v["capabilities"], f"{v['id']} 没有任何能力，不该出现在下拉里"
        assert all(p["base_url"] for p in v["profiles"]), f"{v['id']} 有档案缺地址"
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
    # 「自建 / 本地部署」不再进下拉（用户反馈）：它现在就是"未收录的厂商"
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


# ---- 客户覆盖文件：私有化客户的内网网关 / 私有厂商 ----
def test_override_adds_and_replaces_vendors(tmp_path, monkeypatch):
    """客户只属于自己的接入信息写在覆盖文件里：同 id 整条替换、新 id 追加。
    有了它，客户不必每次都走「手动登记」——那是把系统该做的判断推回给客户。"""
    _write_override(tmp_path, monkeypatch, {"vendors": [
        _vendor("my-gw", name="内网网关", base="http://10.0.0.9/v1"),
        _vendor("deepseek", name="DeepSeek（公司代理）", base="http://proxy.corp/v1",
                model="deepseek-chat"),
    ]})
    ids = {v["id"]: v for v in load_catalog()["vendors"]}
    assert ids["my-gw"]["name"] == "内网网关"                       # 追加
    assert ids["deepseek"]["name"] == "DeepSeek（公司代理）"         # 覆盖：内置那条被整条换掉
    assert catalog_problems() == []


def test_override_broken_json_falls_back_to_bundled(tmp_path, monkeypatch):
    """标本可以缺失、可以写坏——但不许阻塞使用，退回内置标本即可。"""
    _write_override(tmp_path, monkeypatch, "{ 这不是 json")
    assert {v["id"] for v in load_catalog()["vendors"]} >= {"bailian", "deepseek"}


def test_override_with_invalid_content_is_ignored_entirely(tmp_path, monkeypatch):
    """合并不合法（例：向量维度不是库里的 1024）→ 整份覆盖忽略，退回内置标本：
    不许把不合法的配置塞进下拉，让人一步步走到写库那一步才炸。"""
    _write_override(tmp_path, monkeypatch, {"vendors": [
        _vendor("bad", name="坏标本", base="http://x/v1", cap_key="embedding",
                model="v2", dim=1536)]})
    assert "bad" not in {v["id"] for v in load_catalog()["vendors"]}


def test_catalog_endpoint_serves_merged_override(engine, db, tmp_path, monkeypatch):
    """覆盖文件要真的出现在界面下拉里（端点上可见），而不只是能被读到。"""
    _write_override(tmp_path, monkeypatch, {"vendors": [
        _vendor("my-gw", name="内网网关", base="http://10.0.0.9/v1")]})
    c = _client(engine, tmp_path)
    names = {v["id"]: v["name"] for v in c.get(CATALOG).json()["vendors"]}
    assert names["my-gw"] == "内网网关"
    assert names["bailian"] == "阿里百炼"          # 内置的仍在（覆盖不是替换整份标本）
