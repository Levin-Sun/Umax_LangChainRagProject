# 首登强改密（初始化向导收尾）：口令非本人设定的账号（播种 admin / 管理员代建代重置的成员）
# must_change_password=True，未改密前所有受护端点 428 Precondition Required；
# 仅 /auth/me、/auth/logout、/auth/change-password 豁免。
# 428 与 403 分轨：403 仍专属 admin 面（双轨守卫不被稀释），428 专属"口令非本人设定"门闸。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import build_production_app, create_app
from app.models import User
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


@pytest.fixture
def client(engine, db, tmp_path):
    app = create_app(engine=engine, secret="gate-secret", embedder=None,
                     chat_fn=None, upload_dir=str(tmp_path))
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER, role="member")
    seed_user(engine, "fresh@umax.local", "Fresh-Pass-123", role="member",
              must_change_password=True)
    return TestClient(app)


def test_flagged_user_gated_428_exempt_only_auth_endpoints(client):
    login(client, "fresh@umax.local", "Fresh-Pass-123")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200  # 豁免：前端要靠它知道"该弹改密框"
    assert me.json()["must_change_password"] is True
    assert client.post("/api/v1/auth/logout").status_code == 204  # 豁免：随时可走人
    assert client.get("/api/v1/auth/me").status_code == 401  # 登出后即无会话
    login(client, "fresh@umax.local", "Fresh-Pass-123")
    assert client.get("/api/v1/kb").status_code == 428
    assert client.post("/api/v1/chat", json={"question": "q"}).status_code == 428
    assert client.get("/api/v1/users").status_code == 428
    assert client.get("/api/v1/audit").status_code == 428
    for r in [client.get("/api/v1/kb"), client.post("/api/v1/chat", json={"question": "q"}),
              client.get("/api/v1/users"), client.get("/api/v1/audit")]:
        assert r.json()["detail"] == "首次登录必须修改初始口令"


def test_change_password_clears_gate(client):
    login(client, "fresh@umax.local", "Fresh-Pass-123")
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": "Fresh-Pass-123", "new_password": "New-Pass-456"})
    assert r.status_code == 204
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is False
    assert client.get("/api/v1/kb").status_code == 200  # 门闸解除，业务端点放行
    # 改密后的口令可复登录（别的设备会话被吊销是既有语义，这里只验口令本身生效）
    client.post("/api/v1/auth/logout")
    login(client, "fresh@umax.local", "New-Pass-456")
    assert client.get("/api/v1/kb").status_code == 200


def test_change_password_wrong_old_keeps_gate(client):
    login(client, "fresh@umax.local", "Fresh-Pass-123")
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": "Wrong-Pass-000", "new_password": "New-Pass-456"})
    assert r.status_code == 401
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is True
    assert client.get("/api/v1/kb").status_code == 428


def test_admin_created_user_starts_flagged(client):
    login(client, *ADMIN)
    r = client.post("/api/v1/users", json={"email": "new@umax.local", "name": "新人",
                                           "password": "Temp-Pass-123", "role": "member"})
    assert r.status_code == 201
    client.post("/api/v1/auth/logout")
    login(client, "new@umax.local", "Temp-Pass-123")
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is True
    assert client.get("/api/v1/kb").status_code == 428
    # 本人改密后解除
    assert client.post("/api/v1/auth/change-password",
                       json={"old_password": "Temp-Pass-123",
                             "new_password": "Own-Pass-456"}).status_code == 204
    assert client.get("/api/v1/kb").status_code == 200


def test_admin_password_reset_reflags_target_but_not_self(client, engine):
    login(client, *MEMBER)
    assert client.post("/api/v1/auth/change-password",
                       json={"old_password": MEMBER[1], "new_password": "Own-Pass-123"}).status_code == 204
    assert client.get("/api/v1/kb").status_code == 200  # 自己选的口令，不设门闸

    login(client, *ADMIN)
    uid = _uid(engine, MEMBER[0])
    assert client.patch(f"/api/v1/users/{uid}", json={"password": "Reset-Pass-9"}).status_code == 200
    # admin 重置了别人的口令：目标回到门闸后（新口令是 admin 给的，非本人设定）
    client.post("/api/v1/auth/logout")
    login(client, MEMBER[0], "Reset-Pass-9")
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is True
    assert client.get("/api/v1/kb").status_code == 428

    # admin 经 PATCH 重置自己的口令：口令仍是本人输的，不设门闸（否则自锁进改密循环）
    client.post("/api/v1/auth/logout")
    login(client, *ADMIN)   # 前面登出的是 admin 会话；重置自己需要 admin 登录态
    aid = _uid(engine, ADMIN[0])
    assert client.patch(f"/api/v1/users/{aid}", json={"password": "Self-Reset-9"}).status_code == 200
    assert client.get("/api/v1/kb").status_code == 200


def _uid(engine, email: str) -> int:
    with Session(engine) as s:
        return s.query(User).filter_by(email=email).first().id


def test_seeded_admin_flagged_and_migration_idempotent(engine, db, tmp_path):
    """生产装配：播种 admin 带门闸标记；重复装配（幂等 ADD COLUMN）不炸。"""
    build_production_app(upload_dir=str(tmp_path), engine=engine)
    build_production_app(upload_dir=str(tmp_path), engine=engine)  # 第二次跑同一库
    client = TestClient(build_production_app(upload_dir=str(tmp_path), engine=engine))
    from app.core.config import get_settings
    s = get_settings()
    login(client, s.admin_email, s.admin_password)
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is True
    assert client.get("/api/v1/kb").status_code == 428  # 初始化向导第一屏：先改密
