# 审计闭环：每类写操作落对应事件 + 查询过滤/分页 + detail 无明文
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.conftest import login, seed_user
from tests.test_models_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


@pytest.fixture
def client(engine, db, tmp_path):
    c = TestClient(create_app(engine=engine, secret="audit-secret",
                              embedder=FakeEmbedder(get_settings().embedding_dim),
                              chat_fn=lambda q, h: {"answer": "a", "prompt_tokens": 1,
                                                    "completion_tokens": 1},
                              upload_dir=str(tmp_path)))
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    return c


def _actions(client):
    return [r["action"] for r in client.get("/api/v1/audit").json()]


def test_write_paths_emit_events(client, tmp_path):
    kb = client.post("/api/v1/kb", json={"name": "kbAudit"}).json()
    files = {"file": ("a.txt", "内容足够切成块".encode("utf-8") * 10, "text/plain")}
    doc = client.post(f"/api/v1/kb/{kb['id']}/documents", files=files).json()
    client.post(f"/api/v1/documents/{doc['id']}/reprocess")
    m = client.post("/api/v1/models", json={"scenario": "chat", "provider": "p",
                                            "base_url": "http://x/v1", "api_key": "sk-k",
                                            "model_name": "m"}).json()
    client.patch(f"/api/v1/models/{m['id']}", json={"enabled": False})
    client.delete(f"/api/v1/models/{m['id']}")
    assert set(["kb_created", "document_uploaded", "document_reprocessed",
                "model_created", "model_updated", "model_deleted"]) <= set(_actions(client))


def test_documents_delete_event(client, engine, db):
    # 删库/删文档端点阶段 1 不存在，本任务无事件可测——ACTIONS 已预留 kb_deleted/
    # document_deleted（spec §5），未来加端点时按 test_write_paths_events 同型补断言。
    from app.services.audit import ACTIONS
    assert {"kb_deleted", "document_deleted"} <= ACTIONS


def test_audit_query_filters_and_paging(client):
    login_anon = TestClient(client.app)
    login_anon.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": "bad"})
    # 评审收编⑪：原先只有 2 条事件，page2 恒空 → "跨页 disjoint"断言空转。
    # 补 5 次写操作（各一条 kb_created），保证两页都有行、翻页真的是同一序列的两段。
    for i in range(5):
        assert client.post("/api/v1/kb", json={"name": f"kb{i}"}).status_code == 201
    assert len(client.get("/api/v1/audit", params={"limit": 200}).json()) >= 6, "事件量自证"
    r = client.get("/api/v1/audit", params={"action": "login_failed"})
    rows = r.json()
    assert rows and all(x["action"] == "login_failed" for x in rows)
    assert all(x["user_email"] == ADMIN[0] for x in rows)
    assert client.get("/api/v1/audit", params={"user": "ghost@x.com"}).json() == []
    assert len(client.get("/api/v1/audit", params={"limit": 1}).json()) == 1
    page1 = client.get("/api/v1/audit", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/audit", params={"limit": 2, "offset": 2}).json()
    assert len(page1) == len(page2) == 2, "两页都必须取到行，disjoint 才不是空转"
    assert page1 and {x["id"] for x in page1}.isdisjoint({x["id"] for x in page2})
    ids = [x["id"] for x in page1]
    assert ids == sorted(ids, reverse=True), "倒序返回（created_at,id 双键，测试内即 id 倒序）"


def test_audit_requires_admin(client):
    anon = TestClient(client.app)
    assert anon.get("/api/v1/audit").status_code == 401


def test_detail_never_contains_secrets(client):
    client.post("/api/v1/users", json={"email": "sec@x.com", "name": "s",
                                       "password": "Super-Secret-PW", "role": "member"})
    raw = client.get("/api/v1/audit", params={"action": "user_created"}).text
    assert "Super-Secret-PW" not in raw
