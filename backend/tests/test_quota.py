# 用户级配额与告警（§C「AI 要花钱，得有闸门」；此前只有 API key 有配额，花钱的登录用户反而没有）。
# 语义：NULL=不限（默认，开箱不影响既有部署）；达到或超过即拦（429），拦在调模型之前（不白花钱）。
# 预警：达到阈值（配置中心 quota_warn_ratio，默认 0.8）在自助视图标 near_limit，前端出提示条。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import UsageRecord
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


def _client(engine, tmp_path, calls: list | None = None):
    """calls 记录每次生成调用——用来证明"被拦的请求没有真的花钱"。"""

    def fake_chat(q, h):
        if calls is not None:
            calls.append(q)
        return {"answer": "答[1]", "prompt_tokens": 10, "completion_tokens": 5}

    return TestClient(create_app(engine=engine, secret="q-secret", embedder=None,
                                 chat_fn=fake_chat, upload_dir=str(tmp_path)))


def _setup(engine, tmp_path, calls: list | None = None, *, extra_member: str | None = None):
    """装配：管理员 + member（+可选第三用户）、一个含文档的库、并把库授权给 member。

    授权这步不能省：检索层钳制会让未授权用户 MISS，MISS 不调模型也就不产生用量，
    配额测试会退化成"永远不超"的假绿。
    """
    c = _client(engine, tmp_path, calls)
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER, role="member")
    if extra_member:
        seed_user(engine, extra_member, "Other-Pass-1", role="member")
    login(c, *ADMIN)
    kb = c.post("/api/v1/kb", json={"name": "库"}).json()
    raw = "售后规则：生鲜商品不支持七天无理由退货。" * 5
    c.post(f"/api/v1/kb/{kb['id']}/documents",
           files={"file": ("a.txt", raw.encode(), "text/plain")})
    grant_to = [MEMBER[0]] + ([extra_member] if extra_member else [])
    for email in grant_to:
        c.put(f"/api/v1/users/{_uid(engine, email)}/grants", json={"kb_ids": [kb["id"]]})
    return c, kb["id"]


@pytest.fixture
def client(engine, db, tmp_path):
    return _setup(engine, tmp_path)[0]


def _ask(c, q="生鲜能退吗"):
    return c.post("/api/v1/chat", json={"question": q})


def _set_limits(c, uid, **kw):
    return c.patch(f"/api/v1/users/{uid}", json=kw)





def _uid(engine, email):
    from app.models import User
    with Session(engine) as s:
        return s.query(User).filter_by(email=email).first().id


# ---------------- 门闸 ----------------

def test_no_limits_by_default_stays_unlimited(client):
    login(client, *MEMBER)
    for _ in range(3):
        assert _ask(client).status_code == 200
    assert client.get("/api/v1/usage/me").json() == {
        "daily_used": 45, "daily_limit": None, "monthly_used": 45, "monthly_limit": None,
        "near_limit": False, "exceeded": False, "warn_ratio": 0.8}


def test_monthly_limit_blocks_at_threshold_and_stops_spending(engine, db, tmp_path):
    calls: list = []
    c, _kb = _setup(engine, tmp_path, calls)
    uid = _uid(engine, MEMBER[0])
    assert _set_limits(c, uid, monthly_token_limit=30).status_code == 200   # 一次 15 tokens

    login(c, *MEMBER)
    assert _ask(c).status_code == 200          # 15/30
    assert _ask(c).status_code == 200          # 30/30 恰好到线
    before = len(calls)
    r = _ask(c)                                # 再问即拦
    assert r.status_code == 429
    assert "配额" in r.json()["detail"] and "本月" in r.json()["detail"]
    assert len(calls) == before, "被拦的请求不得调用模型（否则闸门不省成本）"
    me = c.get("/api/v1/usage/me").json()
    assert me["exceeded"] is True and me["monthly_used"] == 30 and me["monthly_limit"] == 30


def test_daily_limit_is_independent_from_monthly(engine, db, tmp_path):
    c, _kb = _setup(engine, tmp_path)
    _set_limits(c, _uid(engine, MEMBER[0]), daily_token_limit=15)
    login(c, *MEMBER)
    assert _ask(c).status_code == 200          # 今日 15/15
    r = _ask(c)
    assert r.status_code == 429 and "今日" in r.json()["detail"]
    me = c.get("/api/v1/usage/me").json()
    assert me["daily_used"] == 15 and me["monthly_limit"] is None   # 月不限
    assert me["exceeded"] is True


def test_quota_does_not_block_admin_management_or_others(engine, db, tmp_path):
    c, _kb = _setup(engine, tmp_path, extra_member="other@umax.local")
    _set_limits(c, _uid(engine, MEMBER[0]), monthly_token_limit=0)   # 一刀切零额度
    assert c.get("/api/v1/users").status_code == 200                 # 管理面照常
    login(c, *MEMBER)
    assert _ask(c).status_code == 429
    login(c, "other@umax.local", "Other-Pass-1")                     # 别人不受影响（未设限额）
    assert _ask(c).status_code == 200
    assert c.get("/api/v1/usage/me").json()["monthly_used"] == 15    # 真的调用了模型（非 MISS）


def test_near_limit_flag_follows_warn_ratio_config(client, engine, db):
    login(client, *ADMIN)
    uid = _uid(engine, MEMBER[0])
    _set_limits(client, uid, monthly_token_limit=100)
    login(client, *MEMBER)
    _ask(client)                                    # 15/100
    assert client.get("/api/v1/usage/me").json()["near_limit"] is False
    login(client, *ADMIN)
    client.put("/api/v1/settings", json={"quota_warn_ratio": 0.1})   # 阈值调到 10% → 15% 已越线
    login(client, *MEMBER)
    assert client.get("/api/v1/usage/me").json()["near_limit"] is True


# ---------------- 管理视图 ----------------

def test_usage_users_view_lists_consumption_and_limits(client, engine):
    login(client, *ADMIN)
    uid = _uid(engine, MEMBER[0])
    _set_limits(client, uid, daily_token_limit=1000, monthly_token_limit=2000)
    login(client, *MEMBER)
    _ask(client)
    login(client, *ADMIN)
    rows = client.get("/api/v1/usage/users").json()
    member = next(r for r in rows if r["email"] == MEMBER[0])
    assert member["daily_used"] == 15 and member["monthly_used"] == 15
    assert member["daily_limit"] == 1000 and member["monthly_limit"] == 2000
    assert member["exceeded"] is False
    assert {r["email"] for r in rows} >= {ADMIN[0], MEMBER[0]}       # 未消费用户也在册（用量 0）


def test_usage_users_is_admin_only(client):
    login(client, *MEMBER)
    assert client.get("/api/v1/usage/users").status_code == 403


def test_user_crud_carries_limits_and_validates(client, engine):
    login(client, *ADMIN)
    created = client.post("/api/v1/users", json={
        "email": "q@umax.local", "name": "配额用户", "password": "Passw0rd-1",
        "daily_token_limit": 500, "monthly_token_limit": 5000}).json()
    assert created["daily_token_limit"] == 500 and created["monthly_token_limit"] == 5000
    assert next(u for u in client.get("/api/v1/users").json()
                if u["email"] == "q@umax.local")["monthly_token_limit"] == 5000
    # 改限额 / 清空限额（null=不限）
    assert _set_limits(client, created["id"], monthly_token_limit=None).status_code == 200
    assert next(u for u in client.get("/api/v1/users").json()
                if u["email"] == "q@umax.local")["monthly_token_limit"] is None
    # 负数被 schema 拒（minimum: 0 进 spec）
    assert _set_limits(client, created["id"], daily_token_limit=-1).status_code == 422


def test_usage_records_attribute_to_real_user(client, engine):
    """台账归真实登录者（配额聚合同一口径），不是占位邮箱。"""
    login(client, *MEMBER)
    _ask(client)
    with Session(engine) as s:
        rows = s.query(UsageRecord).filter_by(user_email=MEMBER[0]).all()
    assert len(rows) == 1 and rows[0].prompt_tokens == 10 and rows[0].completion_tokens == 5
