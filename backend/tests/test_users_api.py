# 用户/授权端点：admin 独占、自身保护、吊销链、审计事件——逐条对应 spec §3.1
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


@pytest.fixture
def client(engine, db, tmp_path):
    c = TestClient(create_app(engine=engine, secret="u-secret", upload_dir=str(tmp_path)))
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER)
    login(c, *ADMIN)
    return c


def test_member_forbidden_and_anon_401_on_users_surface(client, engine, tmp_path):
    anon = TestClient(client.app)
    assert anon.get("/api/v1/users").status_code == 401
    login(anon, *MEMBER)
    for method, url in [("get", "/api/v1/users"), ("post", "/api/v1/users")]:
        # httpx/TestClient 的 .get() 快捷方法无 json 参数（只有 .request()/post 有）——GET 不带体，
        # 与 brief 里 json=None 同义；POST 带完整建号体
        kw = {"json": {"email": "x@y.com", "name": "x",
                       "password": "Passw0rd-1", "role": "member"}} if method == "post" else {}
        r = getattr(anon, method)(url, **kw)
        assert r.status_code == 403 and r.json()["detail"] == "需要管理员权限"


def test_create_list_update_flow(client, engine):
    r = client.post("/api/v1/users", json={"email": "new@x.com", "name": "新人",
                                           "password": "New-Pass-1", "role": "member"})
    assert r.status_code == 201
    u = r.json()
    assert u["kb_ids"] == [] and u["status"] == "active" and "hashed_password" not in u
    assert u["created_at"][:2] == "20"  # ISO 时间串（spec §3.1 列表列；[:2] 卡世纪，brief 的 [:4]=="20" 自相矛盾）
    rows = client.get("/api/v1/users").json()
    assert {x["email"] for x in rows} == {"admin@umax.local", "dev@umax.local", "new@x.com"}
    r = client.patch(f"/api/v1/users/{u['id']}", json={"name": "改名", "role": "admin"})
    assert r.json()["name"] == "改名" and r.json()["role"] == "admin"


def test_duplicate_email_400_and_bad_role_400(client):
    assert client.post("/api/v1/users", json={"email": "dev@umax.local", "name": "d",
                                              "password": "Passw0rd-1", "role": "member"}).status_code == 400
    assert client.post("/api/v1/users", json={"email": "ok@x.com", "name": "n",
                                              "password": "Passw0rd-1", "role": "root"}).status_code == 400


def test_self_demote_and_self_disable_are_400(client, engine):
    me = client.get("/api/v1/auth/me").json()
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == me["email"]][0]
    assert client.patch(f"/api/v1/users/{uid}", json={"role": "member"}).status_code == 400
    assert client.patch(f"/api/v1/users/{uid}", json={"status": "disabled"}).status_code == 400


def test_password_reset_kicks_target_sessions_but_not_operator(client, engine):
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == MEMBER[0]][0]
    target = TestClient(client.app)
    login(target, *MEMBER)
    assert client.patch(f"/api/v1/users/{uid}", json={"password": "Reset-Pass-9"}).status_code == 200
    assert target.get("/api/v1/auth/me").status_code == 401, "被重置者即刻踢出"
    assert client.get("/api/v1/auth/me").status_code == 200, "操作者会话不受影响"
    login(target, MEMBER[0], "Reset-Pass-9")          # 新口令可重新登录


def test_disable_kicks_sessions(client, engine):
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == MEMBER[0]][0]
    target = TestClient(client.app)
    login(target, *MEMBER)
    assert client.patch(f"/api/v1/users/{uid}", json={"status": "disabled"}).status_code == 200
    assert target.get("/api/v1/auth/me").status_code == 401
    assert target.post("/api/v1/auth/login",
                       json={"email": MEMBER[0], "password": MEMBER[1]}).status_code == 401, "禁用账号登不进"


def test_grants_put_get_and_admin_refused(client, engine, db):
    from app.models import KnowledgeBase
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        a, b = KnowledgeBase(tenant_id="default", name="A"), KnowledgeBase(tenant_id="default", name="B")
        s.add_all([a, b])
        s.commit()
        ids = [a.id, b.id]
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == MEMBER[0]][0]
    aid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == ADMIN[0]][0]
    assert client.get(f"/api/v1/users/{aid}/grants").status_code == 400
    assert client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": [ids[1], ids[0]]}).status_code == 200
    assert client.get(f"/api/v1/users/{uid}/grants").json() == sorted(ids)
    assert client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": [999999]}).status_code == 400
    assert client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": [ids[0]]}).status_code == 200
    assert client.get(f"/api/v1/users/{uid}/grants").json() == [ids[0]], "PUT=整集合替换"


def test_users_audit_events(client, engine):
    from app.models import AuditLog
    from sqlalchemy.orm import Session
    r = client.post("/api/v1/users", json={"email": "aud@x.com", "name": "a",
                                           "password": "Passw0rd-1", "role": "member"})
    uid = r.json()["id"]
    client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": []})
    client.patch(f"/api/v1/users/{uid}", json={"status": "disabled"})
    with Session(engine) as s:
        rows = [(a.action, a.target_id, a.detail) for a in s.query(AuditLog).filter(
            AuditLog.action.in_(["user_created", "grants_updated", "user_updated"]))]
    assert (rows[0][0], rows[0][1]) == ("user_created", uid)
    assert ("grants_updated", uid) in [(x[0], x[1]) for x in rows]
    assert any(x[0] == "user_updated" and x[2] == {"fields": ["status"]} for x in rows)
