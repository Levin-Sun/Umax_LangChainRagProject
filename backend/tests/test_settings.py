# 配置中心（§D「配置驱动」的运行时载体）：提示词/检索/切块参数后台可改、改完即生效。
# app_settings 是通用键值表，本模块是其白名单门面——只有登记在 SPEC 里的键可读写。
# 单字段约束（类型/范围/长度）写进 pydantic 模型 → 进 spec（422 有声明）；跨字段关系
# （chunk_min ≤ chunk_target）OpenAPI 表达不了，不做硬拒，改为响应里给 warnings 提示。
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.chat import SYSTEM_PROMPT
from app.services.settings import (DEFAULT_MISS_ANSWER, SettingsStore, build_spec)
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


def _client(engine, tmp_path, **kw):
    return TestClient(create_app(engine=engine, secret="cfg-secret", embedder=None,
                                 chat_fn=lambda q, h: {"answer": "a[1]", "prompt_tokens": 1,
                                                       "completion_tokens": 1},
                                 upload_dir=str(tmp_path), **kw))


@pytest.fixture
def client(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER, role="member")
    return c


# ---------------- 服务层 ----------------

def test_defaults_come_from_env_and_effective_applies_overrides(engine, db):
    store = SettingsStore(engine)
    d = store.defaults()
    assert d["chat_system_prompt"] == SYSTEM_PROMPT
    assert d["chat_miss_answer"] == DEFAULT_MISS_ANSWER
    assert d["recall_k"] == 10 and d["rerank_top_n"] == 5 and d["chunk_target"] == 300
    assert store.overrides() == {}
    assert store.effective()["recall_k"] == 10   # 无覆盖即默认


def test_put_validates_and_only_accepts_whitelisted_keys(client):
    login(client, *ADMIN)
    body = client.put("/api/v1/settings", json={"recall_k": 12}).json()
    assert body["values"]["recall_k"] == 12 and body["overridden"] == ["recall_k"]
    assert body["values"]["rerank_top_n"] == 5            # 未动的字段保持默认
    # 未知键被模型拒（422，additionalProperties=false 进 spec）
    assert client.put("/api/v1/settings", json={"nope_key": 1}).status_code == 422
    # 越界被 schema 约束拒
    assert client.put("/api/v1/settings", json={"recall_k": 999}).status_code == 422
    assert client.put("/api/v1/settings", json={"min_sim": 2.5}).status_code == 422
    assert client.put("/api/v1/settings", json={"chat_miss_answer": "x" * 201}).status_code == 422


def test_null_restores_default_and_empty_put_is_noop(client):
    login(client, *ADMIN)
    client.put("/api/v1/settings", json={"chat_miss_answer": "自定文案"})
    assert client.get("/api/v1/settings").json()["overridden"] == ["chat_miss_answer"]
    body = client.put("/api/v1/settings", json={"chat_miss_answer": None}).json()
    assert body["values"]["chat_miss_answer"] == DEFAULT_MISS_ANSWER
    assert body["overridden"] == []
    assert client.put("/api/v1/settings", json={}).json()["overridden"] == []


def test_cross_field_relation_reported_as_warning_not_error(client):
    login(client, *ADMIN)
    body = client.put("/api/v1/settings",
                      json={"chunk_target": 150, "chunk_min": 400}).json()
    assert body["values"]["chunk_min"] == 400      # 不硬拒、不偷偷改值
    assert any("chunk_min" in w for w in body["warnings"])
    assert client.get("/api/v1/settings").json()["warnings"]


# ---------------- 端点：权限与审计 ----------------

def test_settings_endpoints_are_admin_only(client, engine):
    assert client.get("/api/v1/settings").status_code == 401
    assert client.put("/api/v1/settings", json={}).status_code == 401
    login(client, *MEMBER)
    assert client.get("/api/v1/settings").status_code == 403
    assert client.put("/api/v1/settings", json={}).status_code == 403


def test_change_is_audited_but_noop_is_not(client):
    login(client, *ADMIN)
    client.put("/api/v1/settings", json={"recall_k": 7})
    client.put("/api/v1/settings", json={})                # 空改不记
    client.put("/api/v1/settings", json={"recall_k": 7})   # 同值不记
    rows = client.get("/api/v1/audit", params={"action": "settings_updated"}).json()
    assert len(rows) == 1 and rows[0]["detail"]["fields"] == ["recall_k"]


# ---------------- 生效链路：改完即生效 ----------------

def test_miss_answer_override_takes_effect_immediately(client):
    login(client, *ADMIN)
    client.put("/api/v1/settings", json={"chat_miss_answer": "知识库里没有找到依据。"})
    out = client.post("/api/v1/chat", json={"question": "完全不相关的问法"}).json()
    assert out["answer"] == "知识库里没有找到依据。"


def test_retrieval_top_n_override_limits_citations(client):
    login(client, *ADMIN)
    kb = client.post("/api/v1/kb", json={"name": "k"}).json()
    for i, txt in enumerate(["退货规则：七天无理由退货", "退货流程：联系客服申请",
                             "退货时效：三个工作日处理"]):
        client.post(f"/api/v1/kb/{kb['id']}/documents",
                    files={"file": (f"d{i}.txt", (txt * 8).encode(), "text/plain")})
    full = client.post("/api/v1/chat", json={"question": "退货", "kb_ids": [kb["id"]]}).json()
    assert len(full["citations"]) == 3
    client.put("/api/v1/settings", json={"rerank_top_n": 1})
    trimmed = client.post("/api/v1/chat", json={"question": "退货", "kb_ids": [kb["id"]]}).json()
    assert len(trimmed["citations"]) == 1


def test_chunk_target_applies_to_new_kb_while_existing_kb_keeps_its_lock(client):
    """§3.3「建库时锁定切分参数」的两面：老库锁死不动（重建索引也按锁定的来），
    新建库才吃到后台刚改的配置。"""
    login(client, *ADMIN)
    raw = ("\n".join([f"第{i}条规则说明内容，用于验证切块参数是否生效。" * 6
                     for i in range(30)])).encode()
    old_kb = client.post("/api/v1/kb", json={"name": "老库"}).json()
    old_doc = client.post(f"/api/v1/kb/{old_kb['id']}/documents",
                          files={"file": ("big.txt", raw, "text/plain")}).json()
    before = len(client.get(f"/api/v1/documents/{old_doc['id']}/chunks").json())
    assert before > 1
    # 后台把切块目标调大 4 倍；老库已锁定旧值 → 重建索引块数不变（锁定语义）
    client.put("/api/v1/settings", json={"chunk_target": 1200})
    assert client.post(f"/api/v1/documents/{old_doc['id']}/reprocess").status_code == 202
    assert len(client.get(f"/api/v1/documents/{old_doc['id']}/chunks").json()) == before
    # 新建库锁定新值 → 同样文本切出的块数明显变少（配置真的生效）
    new_kb = client.post("/api/v1/kb", json={"name": "新库"}).json()
    new_doc = client.post(f"/api/v1/kb/{new_kb['id']}/documents",
                          files={"file": ("big.txt", raw, "text/plain")}).json()
    assert len(client.get(f"/api/v1/documents/{new_doc['id']}/chunks").json()) < before


def test_custom_system_prompt_reaches_model(engine, db):
    """生成层提示词经 provider 注入：自定义提示词真的进了请求体（协议不变、fakes 不受影响）。"""
    from app.services.chat import ChatClient, make_chat_fn

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json_loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    store = SettingsStore(engine)
    prompt = "你是某电商的售后助手，只用资料回答并标注来源。"
    client_ = ChatClient(api_key="k", base_url="http://t/v1", model="m",
                         transport=httpx.MockTransport(handler))
    fn = make_chat_fn(client_, system_prompt=lambda: store.effective()["chat_system_prompt"])
    fn("问题", [{"doc_name": "a.txt", "content": "内容"}])
    assert seen["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}  # 未改=默认
    with __import__("sqlalchemy.orm", fromlist=["Session"]).Session(engine) as s:
        store.put(s, {"chat_system_prompt": prompt}, "admin@umax.local")
        s.commit()
    fn("问题", [{"doc_name": "a.txt", "content": "内容"}])
    assert seen["messages"][0] == {"role": "system", "content": prompt}         # 改后即时生效


def json_loads(b: bytes) -> dict:
    import json
    return json.loads(b)


def test_spec_covers_prompts_and_params():
    spec = build_spec()
    assert set(spec) == {"chat_system_prompt", "chat_miss_answer", "vision_prompt",
                         "recall_k", "rerank_top_n", "min_sim", "quota_warn_ratio",
                         "chunk_target", "chunk_min"}
    assert spec["recall_k"].kind == "int" and spec["min_sim"].kind == "float"
