# 认证端点全行为：登录/会话/me/登出/自助改密/吊销链/限流——账号体系的心脏，逐条对应 spec §2/§3.1
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.models import User, UserSession
from app.services.auth import hash_password
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


def _client(engine, tmp_path):
    app = create_app(engine=engine, secret="auth-secret", embedder=None,
                     chat_fn=None, upload_dir=str(tmp_path))
    return TestClient(app)


@pytest.fixture
def client(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    seed_user(engine, *ADMIN, role="admin", name="总管")
    seed_user(engine, *MEMBER, role="member", name="小王")
    return c


def test_login_sets_httponly_cookie_and_me_returns_profile(client):
    login(client, *ADMIN)
    me = client.get("/api/v1/auth/me").json()
    assert me == {"email": ADMIN[0], "name": "总管", "role": "admin", "kb_ids": None}
    assert any(c == "umax_session" for c in client.cookies)  # httpx 已托管会话 cookie（Cookies 迭代出名字符串）
    r = client.post("/api/v1/auth/login", json={"email": MEMBER[0], "password": MEMBER[1]})
    assert r.status_code == 204
    sc = r.headers["set-cookie"]
    assert "umax_session=" in sc and "httponly" in sc.lower() and "samesite=lax" in sc.lower()
    assert client.get("/api/v1/auth/me").json()["role"] == "member"  # 新 cookie 覆盖生效


def test_member_me_kb_ids_is_sorted_grants(client, engine, db):
    from app.models import KnowledgeBase, UserKbGrant
    from sqlalchemy.orm import Session
    kb_a = KnowledgeBase(tenant_id="default", name="A")
    kb_b = KnowledgeBase(tenant_id="default", name="B")
    with Session(engine) as s:
        s.add_all([kb_a, kb_b])
        s.commit()
        uid = s.query(User).filter_by(email=MEMBER[0]).first().id
        s.add_all([UserKbGrant(user_id=uid, kb_id=kb_b.id), UserKbGrant(user_id=uid, kb_id=kb_a.id)])
        s.commit()
        # 会话内取值：expunge 后 commit 使属性过期，Session 关闭再读即 DetachedInstanceError
        want = sorted([kb_a.id, kb_b.id])
    login(client, *MEMBER)
    me = client.get("/api/v1/auth/me").json()
    assert me["kb_ids"] == want


@pytest.mark.parametrize("email,password", [(ADMIN[0], "nope"), ("ghost@x.com", "whatever")])
def test_login_401_uniform_no_enumeration(client, email, password):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401 and r.json()["detail"] == "邮箱或口令错误"
    assert client.get("/api/v1/auth/me").status_code == 401  # 失败不发会话


def test_throttle_429_after_10_failures(client):
    for _ in range(10):
        assert client.post("/api/v1/auth/login",
                           json={"email": ADMIN[0], "password": "bad"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": "bad"})
    assert r.status_code == 429
    r2 = client.post("/api/v1/auth/login", json={"email": MEMBER[0], "password": MEMBER[1]})
    assert r2.status_code == 204, "限流键含邮箱，不牵连他人"


def test_unauthenticated_me_and_logout_are_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_logout_clears_server_session(client, engine):
    login(client, *ADMIN)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        assert s.query(UserSession).count() == 0, "吊销=删行，cookie 残值作废"


def test_expired_session_rejected(client, engine):
    login(client, *ADMIN)
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        row = s.query(UserSession).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_disabled_user_existing_session_dies(client, engine, db):
    from sqlalchemy.orm import Session
    login(client, *MEMBER)
    with Session(engine) as s:
        s.query(User).filter_by(email=MEMBER[0]).first().status = "disabled"
        s.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_change_password_verifies_old_and_kicks_other_sessions(client, engine):
    from sqlalchemy.orm import Session
    login(client, *MEMBER)
    other = TestClient(client.app)                    # 同账号第二设备也在线
    login(other, *MEMBER)
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": "wrong-old", "new_password": "New-Pass-9"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": MEMBER[1], "new_password": "New-Pass-9"})
    assert r.status_code == 204
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == MEMBER[0], "当前会话改密后仍活"
    assert other.get("/api/v1/auth/me").status_code == 401, "他人会话即刻作废"
    with Session(engine) as s:
        assert s.query(UserSession).count() == 1, "旧会话全部吊销，只留当前"
    # 旧口令已失效：401；新口令可重新登录
    anon = TestClient(client.app)
    assert anon.post("/api/v1/auth/login", json={"email": MEMBER[0], "password": MEMBER[1]}).status_code == 401
    login(anon, MEMBER[0], "New-Pass-9")


def test_change_password_rejects_short_new(client):
    login(client, *ADMIN)
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": ADMIN[1], "new_password": "short"})
    assert r.status_code == 422


def test_login_audit_events(client, engine, db):
    from app.models import AuditLog
    from sqlalchemy.orm import Session
    login(client, *ADMIN)
    client.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": "bad"})
    client.post("/api/v1/auth/logout")
    with Session(engine) as s:
        rows = s.query(AuditLog).order_by(AuditLog.id).all()
    assert [a.action for a in rows] == ["login_success", "login_failed", "logout"]
    assert all(a.ip for a in rows), "认证事件必带来源 ip"
