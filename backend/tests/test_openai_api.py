# 开放 API（阶段 2·§2.2）：API key 管理（admin 面 CRUD）+ OpenAI 兼容端点（Bearer key）。
# key 明文只在创建响应出现一次，库中只落 SHA-256；作用域（kb_ids）在检索层钳制——
# key 没授权的库即使显式点名也召回不到；配额按自然月 token 计，超了 429。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import ApiKey, KnowledgeBase, UsageRecord
from tests.conftest import login, seed_user

from tests.test_models_api import FakeEmbedder
from app.core.config import get_settings

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


def fake_chat(question, hits):
    first = hits[0]["doc_name"] if hits else ""
    return {"answer": f"答[{first}][1]", "prompt_tokens": 10, "completion_tokens": 5,
            "model": "fake-chat-model"}


@pytest.fixture
def client(engine, db, tmp_path):
    app = create_app(engine=engine, secret="openapi-secret",
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     chat_fn=fake_chat, upload_dir=str(tmp_path))
    seed_user(engine, *ADMIN, role="admin")
    c = TestClient(app)
    login(c, *ADMIN)
    for name in ("库A", "库B"):
        c.post("/api/v1/kb", json={"name": name})
    return c


def _make_key(client, **kw):
    body = {"name": "钉钉机器人", **kw}
    r = client.post("/api/v1/api-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(key_plain):
    return {"Authorization": f"Bearer {key_plain}"}


def test_create_key_returns_plaintext_once_and_list_masks(client):
    out = _make_key(client, kb_ids=[1])
    assert out["name"] == "钉钉机器人"
    assert out["key"].startswith("umax-") and len(out["key"]) >= 40   # 明文一次性下发
    assert out["key_prefix"] in out["key"]                            # 前缀=明文打码头的依据
    assert out["kb_ids"] == [1] and out["enabled"] is True
    listed = client.get("/api/v1/api-keys").json()
    assert len(listed) == 1
    row = listed[0]
    assert row["id"] == out["id"]
    assert "key" not in row and row["key_prefix"] == out["key_prefix"]  # 列表永不回明文


def test_create_key_validates_kb_ids(client):
    r = client.post("/api/v1/api-keys", json={"name": "坏 key", "kb_ids": [999]})
    assert r.status_code == 400


def test_openai_endpoint_requires_bearer_and_rejects_bad_key(client):
    r = client.post("/api/v1/openai/chat/completions",
                    json={"messages": [{"role": "user", "content": "q"}]})
    assert r.status_code == 401
    r = client.post("/api/v1/openai/chat/completions",
                    json={"messages": [{"role": "user", "content": "q"}]},
                    headers=_bearer("umax-not-a-real-key"))
    assert r.status_code == 401


def test_disabled_key_is_401(client):
    out = _make_key(client)
    client.patch(f"/api/v1/api-keys/{out['id']}", json={"enabled": False})
    r = client.post("/api/v1/openai/chat/completions",
                    json={"messages": [{"role": "user", "content": "q"}]},
                    headers=_bearer(out["key"]))
    assert r.status_code == 401   # 吊销即失效（DB 行删/禁都一样）


def test_completions_answer_with_citations_and_openai_shape(client):
    out = _make_key(client, kb_ids=[1, 2])
    # 先给库A 灌一篇文档（同步入库），检索才有料
    raw = "售后规则：生鲜商品不支持七天无理由退货。" * 5
    up = client.post("/api/v1/kb/1/documents",
                     files={"file": ("policy.txt", raw.encode(), "text/plain")})
    assert up.status_code == 201
    r = client.post("/api/v1/openai/chat/completions",
                    json={"model": "any-model", "messages": [
                        {"role": "system", "content": "你是客服"},
                        {"role": "user", "content": "生鲜能七天无理由退货吗？"},
                    ]},
                    headers=_bearer(out["key"]))
    assert r.status_code == 200, r.text
    body = r.json()
    # OpenAI 兼容外形：choices[0].message.content + usage 三件套
    assert body["object"] == "chat.completion"
    assert body["model"] == "any-model"
    msg = body["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert "答[" in msg["content"] and "policy.txt" in msg["content"]
    assert body["usage"]["prompt_tokens"] == 10 and body["usage"]["completion_tokens"] == 5
    assert body["usage"]["total_tokens"] == 15
    # 引用溯源照旧给（OpenAI 没这字段，作为本产品扩展字段共存）
    assert body["citations"][0]["doc_name"] == "policy.txt"


def test_scope_clamp_key_restricted_kb(client):
    """key 只授库A：问库B 的内容召回不到（钳制在检索层）。"""
    out = _make_key(client, name="只看A", kb_ids=[1])
    raw_b = "FBA 库存每月十五号前提交补货计划。" * 5
    client.post("/api/v1/kb/2/documents",
                files={"file": ("fba.txt", raw_b.encode(), "text/plain")})
    r = client.post("/api/v1/openai/chat/completions",
                    json={"messages": [{"role": "user", "content": "FBA 补货计划几号提交？"}]},
                    headers=_bearer(out["key"]))
    assert r.status_code == 200
    assert r.json()["citations"] == []          # 库B 的内容召回不到 → 未命中兜底
    assert "资料里没有" in r.json()["choices"][0]["message"]["content"]


def test_monthly_quota_enforced_and_recorded(client, engine):
    # 先灌一篇文档：未命中（MISS）不调模型不记账不耗配额，配额只对真实生成计数
    raw = "售后规则：生鲜商品不支持七天无理由退货。" * 5
    client.post("/api/v1/kb/1/documents",
                files={"file": ("policy.txt", raw.encode(), "text/plain")})
    out = _make_key(client, monthly_token_quota=14)   # 一次调用 15 tokens → 第二次超
    h = _bearer(out["key"])
    payload = {"model": "any-model", "messages": [{"role": "user", "content": "q"}]}
    assert client.post("/api/v1/openai/chat/completions", json=payload, headers=h).status_code == 200
    r = client.post("/api/v1/openai/chat/completions", json=payload, headers=h)
    assert r.status_code == 429
    # 记账落了台账（user_email=apikey:{id}，台账"谁"维度可见），模型名取请求里的 model
    with Session(engine) as s:
        rows = s.query(UsageRecord).filter(
            UsageRecord.user_email == f"apikey:{out['id']}").all()
        assert len(rows) == 1
        assert rows[0].model == "any-model" and rows[0].scenario == "chat"
        assert rows[0].prompt_tokens == 10 and rows[0].completion_tokens == 5


def test_unlimited_quota_never_blocks(client):
    out = _make_key(client)   # monthly_token_quota=None → 不限
    h = _bearer(out["key"])
    payload = {"messages": [{"role": "user", "content": "q"}]}
    for _ in range(3):
        assert client.post("/api/v1/openai/chat/completions", json=payload, headers=h).status_code == 200


def test_openai_endpoint_has_no_user_messages_pollution(client, engine):
    """Bearer 调用不该建会话/消息（会话体系是登录用户的，开放 API 不进历史）。"""
    from app.models import Conversation, Message
    out = _make_key(client)
    client.post("/api/v1/openai/chat/completions",
                json={"messages": [{"role": "user", "content": "q"}]},
                headers=_bearer(out["key"]))
    with Session(engine) as s:
        assert s.query(Conversation).count() == 0
        assert s.query(Message).count() == 0


def test_api_key_admin_surface_and_audit(client, engine):
    # member 不可见（admin 面守卫）
    seed_user(engine, "dev@umax.local", "Dev1-Pass-123", role="member")
    c2 = TestClient(create_app(engine=engine, secret="openapi-secret", embedder=None,
                               chat_fn=fake_chat, upload_dir="/tmp"))
    login(c2, "dev@umax.local", "Dev1-Pass-123")
    assert c2.get("/api/v1/api-keys").status_code == 403

    out = _make_key(client)
    client.patch(f"/api/v1/api-keys/{out['id']}", json={"name": "改名", "monthly_token_quota": 100})
    client.delete(f"/api/v1/api-keys/{out['id']}")
    from app.models import AuditLog
    with Session(engine) as s:
        actions = [a.action for a in s.query(AuditLog).order_by(AuditLog.id)]
    assert "api_key_created" in actions
    assert "api_key_updated" in actions
    assert "api_key_deleted" in actions
    # 审计 detail 不落 key 明文（摘要同样不该出现）
    import json
    with Session(engine) as s:
        details = [a.detail for a in s.query(AuditLog).filter(
            AuditLog.action.in_(["api_key_created", "api_key_updated", "api_key_deleted"]))]
    blob = json.dumps(details, ensure_ascii=False)
    assert out["key"] not in blob
    # 删除后再用旧明文 → 401
    assert client.post("/api/v1/openai/chat/completions",
                       json={"messages": [{"role": "user", "content": "q"}]},
                       headers=_bearer(out["key"])).status_code == 401
