# 白标设置（阶段 2·§2.2）：品牌名+logo 后台可改，"一套系统卖一家像定做的一样"。
# GET /branding 匿名可读（登录页要显品牌，和 /health 同为匿名面）；PUT admin 独占全审计。
# logo 只收 data:image/* base64（避免再开静态文件路由/卷），有大小上限防滥用。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AppSetting, AuditLog
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
DEFAULT_BRAND = "Umax RAG"
PNG_1PX = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
           "h6FO1AAAAABJRU5ErkJggg==")


@pytest.fixture
def client(engine, db, tmp_path):
    app = create_app(engine=engine, secret="brand-secret", embedder=None,
                     chat_fn=None, upload_dir=str(tmp_path))
    seed_user(engine, *ADMIN, role="admin")
    return TestClient(app)


def test_get_branding_anonymous_returns_defaults(client):
    r = client.get("/api/v1/branding")
    assert r.status_code == 200   # 匿名可读：登录页首屏就要显品牌
    assert r.json() == {"brand_name": DEFAULT_BRAND, "logo": None}


def test_put_branding_admin_only_and_audited(client, engine):
    assert client.put("/api/v1/branding", json={"brand_name": "客户牌"}).status_code == 401
    login(client, *ADMIN)
    r = client.put("/api/v1/branding",
                   json={"brand_name": "客户牌知识库", "logo": PNG_1PX})
    assert r.status_code == 200
    # 匿名再读即见新品牌（缓存语义：读库直出，无 session 态）
    out = client.get("/api/v1/branding").json()
    assert out == {"brand_name": "客户牌知识库", "logo": PNG_1PX}
    with Session(engine) as s:
        rows = [(a.action, a.detail) for a in s.query(AuditLog)
                .filter(AuditLog.action == "branding_updated").order_by(AuditLog.id)]
    assert rows and set(rows[0][1]["fields"]) == {"brand_name", "logo"}
    assert PNG_1PX not in str(rows)   # 审计不落数据体（logo base64 太肥，detail 只记字段名）


def test_partial_put_keeps_other_field_and_clearing_logo(client):
    login(client, *ADMIN)
    client.put("/api/v1/branding", json={"brand_name": "客户牌", "logo": PNG_1PX})
    client.put("/api/v1/branding", json={"brand_name": "客户牌2"})   # 只改名字
    out = client.get("/api/v1/branding").json()
    assert out["brand_name"] == "客户牌2" and out["logo"] == PNG_1PX
    client.put("/api/v1/branding", json={"logo": None})             # 显式 null=清掉 logo
    out = client.get("/api/v1/branding").json()
    assert out["brand_name"] == "客户牌2" and out["logo"] is None


def test_logo_validation_and_name_length(client):
    login(client, *ADMIN)
    # 非 data:image 前缀拒
    r = client.put("/api/v1/branding", json={"logo": "https://x/a.png"})
    assert r.status_code == 400
    r = client.put("/api/v1/branding", json={"logo": "data:text/html;base64,PGI+"})
    assert r.status_code == 400
    # 超 400KB 拒
    r = client.put("/api/v1/branding", json={"logo": "data:image/png;base64," + "A" * 400_001})
    assert r.status_code == 400
    # 品牌名空白/超长拒
    assert client.put("/api/v1/branding", json={"brand_name": "   "}).status_code == 422
    assert client.put("/api/v1/branding", json={"brand_name": "字" * 65}).status_code == 422
    # 请求都没生效：值仍是默认
    assert client.get("/api/v1/branding").json()["brand_name"] == DEFAULT_BRAND


def test_settings_table_roundtrip(client, engine):
    """app_settings 是通用键值表（后续提示词/检索参数后台化都走它），白标只是第一个租户。"""
    login(client, *ADMIN)
    client.put("/api/v1/branding", json={"brand_name": "客户牌"})
    with Session(engine) as s:
        row = s.get(AppSetting, "brand_name")
        assert row is not None and row.value == "客户牌"
        assert row.updated_by == ADMIN[0]
