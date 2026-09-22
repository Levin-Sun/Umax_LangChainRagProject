# 管理鉴权（方案1）：ADMIN_TOKEN 配置后管理类端点无会话 401，login 下发 HttpOnly cookie，
# logout 清会话；留空=不启用（开发形态不变）；开放读路径（GET /kb、conversations、health）不受影响。
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.test_models_api import FakeEmbedder

TOKEN = "s3cret-admin-token"


def _client(engine, tmp_path, admin_token=TOKEN):
    def fake_chat(query, hits):
        return {"answer": "ok[1]", "prompt_tokens": 1, "completion_tokens": 1}

    app = create_app(engine=engine, secret="admin-auth-secret",
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     chat_fn=fake_chat, upload_dir=str(tmp_path), admin_token=admin_token)
    return TestClient(app)


@pytest.fixture
def client(engine, db, tmp_path):
    return _client(engine, tmp_path)


PROTECTED = [
    ("post", "/api/v1/kb", {"name": "kb1"}),
    ("get", "/api/v1/models", None),
    ("post", "/api/v1/models", None),
    ("get", "/api/v1/usage/summary", None),
]


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_protected_routes_401_without_session(client, method, path, body):
    resp = client.request(method.upper(), path, json=body)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "需要管理员登录"


def test_open_routes_unaffected(client):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/kb").status_code == 200
    assert client.get("/api/v1/conversations").status_code == 200


def test_login_wrong_token_401(client):
    assert client.post("/api/v1/admin/login", json={"token": "nope"}).status_code == 401
    assert client.get("/api/v1/models").status_code == 401  # 错误口令不发会话


def test_login_sets_httponly_cookie_and_unlocks_admin_routes(client):
    resp = client.post("/api/v1/admin/login", json={"token": TOKEN})
    assert resp.status_code == 204
    raw = resp.headers["set-cookie"]
    assert "admin_session=" in raw and "httponly" in raw.lower() and "samesite=lax" in raw.lower()
    assert client.get("/api/v1/models").status_code == 200
    kb = client.post("/api/v1/kb", json={"name": "kb1"})
    assert kb.status_code == 201
    assert client.get("/api/v1/usage/summary").status_code == 200


def test_logout_clears_session(client):
    client.post("/api/v1/admin/login", json={"token": TOKEN})
    assert client.get("/api/v1/models").status_code == 200
    assert client.post("/api/v1/admin/logout").status_code == 204
    assert client.get("/api/v1/models").status_code == 401


def test_login_sets_visible_hint_cookie_for_nav(client):
    # 前端 Nav/admin 页需要 JS 可读的登录标记（HttpOnly 会话 JS 读不到）；
    # 标记只影响菜单显隐，授权仍只认 admin_session，伪造无安全影响
    resp = client.post("/api/v1/admin/login", json={"token": TOKEN})
    cookies = resp.headers.get_list("set-cookie")
    hint = [c for c in cookies if c.startswith("admin_hint=")]
    assert len(hint) == 1
    assert "httponly" not in hint[0].lower()
    assert "admin_session=" in cookies[0] + cookies[1]  # 会话 cookie 照常 HttpOnly


def test_logout_clears_hint_cookie(client):
    client.post("/api/v1/admin/login", json={"token": TOKEN})
    resp = client.post("/api/v1/admin/logout")
    cookies = resp.headers.get_list("set-cookie")
    assert any(c.startswith("admin_session=") for c in cookies)
    assert any(c.startswith("admin_hint=") for c in cookies)


def test_unconfigured_token_disables_auth(engine, db, tmp_path):
    client2 = _client(engine, tmp_path, admin_token="")
    assert client2.post("/api/v1/kb", json={"name": "kb1"}).status_code == 201
    assert client2.get("/api/v1/models").status_code == 200
    # 未启用时登录一律 401（不泄露"未配置"信息，fuzz 面也只声明 401/400）
    assert client2.post("/api/v1/admin/login", json={"token": ""}).status_code == 401


def test_login_bad_body_400(client):
    # 修复④口径（test_contract_fixes L107）：非 UTF-8 字节体→400 请求体解析失败；坏 JSON 文本仍 422
    resp = client.post("/api/v1/admin/login", content=b"123\xff\xfe",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
