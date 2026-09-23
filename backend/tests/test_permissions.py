# 本期主证据：过滤在检索层（SQL 谓词），不在展示层——A 库内容对无授权 member 的
# 召回/citations/kb 列表/文档列表全不可见；越权 403、跨用户资源 404、空授权 MISS。
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.models import Conversation, KnowledgeBase, UsageRecord, User, UserKbGrant
from app.services.auth import hash_password
from sqlalchemy.orm import Session
from tests.test_models_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


def _fake_chat(q, hits):
    return {"answer": f"依据{len(hits)}段可答[1]", "prompt_tokens": 5, "completion_tokens": 2}


@pytest.fixture
def world(engine, db, tmp_path):
    """A/B 两库各入一篇内容互斥的真实文档（FakeEmbedder 真走向量 SQL 路）。

    注：db 只作清表用（users 的 (tenant_id,email) 唯一约束 + kb/conversation 断言都
    要求"本测试从零开始"，不挂 db 会吃到上一个测试文件的残留行）。
    """
    with Session(engine) as s:
        s.add_all([
            User(tenant_id="default", email=ADMIN[0], name="admin", role="admin", status="active",
                 hashed_password=hash_password(ADMIN[1])),
            User(tenant_id="default", email=MEMBER[0], name="dev", role="member", status="active",
                 hashed_password=hash_password(MEMBER[1])),
        ])
        a, b = KnowledgeBase(tenant_id="default", name="A库"), KnowledgeBase(tenant_id="default", name="B库")
        s.add_all([a, b])
        s.flush()
        ids = {"a": a.id, "b": b.id}
        s.commit()
    app = create_app(engine=engine, secret="perm-secret",
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     chat_fn=_fake_chat, upload_dir=str(tmp_path))
    admin = TestClient(app)
    from tests.conftest import login
    login(admin, *ADMIN)
    for kb_id, content in [(ids["a"], "阿尔法限量款的发货时效是七十二小时"),
                           (ids["b"], "贝塔系列的退换货规则是七天无理由")]:
        kb = admin.post(f"/api/v1/kb/{kb_id}/documents",
                        files={"file": (f"{content[:2]}.txt", content.encode() * 20, "text/plain")})
        assert kb.status_code == 201
    member = TestClient(app)
    login(member, *MEMBER)
    return {"admin": admin, "member": member, "ids": ids, "engine": engine, "app": app}


def _grant(world, kb_key):
    with Session(world["engine"]) as s:
        uid = s.query(User).filter_by(email=MEMBER[0]).first().id
        s.add(UserKbGrant(user_id=uid, kb_id=world["ids"][kb_key]))
        s.commit()


def test_anonymous_cannot_touch_any_business_route(world):
    anon = TestClient(world["app"])
    for method, url in [("get", "/api/v1/kb"), ("get", "/api/v1/conversations"),
                        ("post", "/api/v1/chat"), ("post", "/api/v1/retrieve"),
                        ("get", "/api/v1/usage/summary"), ("get", "/api/v1/models")]:
        # httpx/TestClient 的 .get() 快捷方法无 json 参数（只有 .post()/.request() 有）——
        # 与 test_users_api 同款处理：GET 不带体即与 json=None 同义
        kw = {"json": {}} if method == "post" else {}
        assert getattr(anon, method)(url, **kw).status_code == 401, f"{method.upper()} {url}"
    assert anon.get("/api/v1/health").status_code == 200, "健康检查保持匿名"


def test_member_kb_list_and_documents_scoped(world):
    _grant(world, "b")
    kb_ids = [k["id"] for k in world["member"].get("/api/v1/kb").json()]
    assert kb_ids == [world["ids"]["b"]]
    assert world["member"].get(f"/api/v1/kb/{world['ids']['a']}/documents").status_code == 404
    assert world["admin"].get(f"/api/v1/kb/{world['ids']['a']}/documents").status_code == 200


def test_search_layer_filter_zero_alpha_leak_in_retrieval(world):
    _grant(world, "b")
    rb = world["member"].post("/api/v1/retrieve", json={"query": "贝塔 退换货 规则"})
    assert rb.json(), "授权库 BM25 必须召回（防空集假通过）"
    assert all(h["kb_id"] == world["ids"]["b"] for h in rb.json())
    ra = world["member"].post("/api/v1/retrieve", json={"query": "阿尔法 发货 时效"})
    assert all(h["kb_id"] != world["ids"]["a"] for h in ra.json()), "检索结果不得含未授权库的块"
    r2 = world["member"].post("/api/v1/chat", json={"question": "阿尔法限量款的发货时效是多久"})
    for c in r2.json()["citations"]:
        assert "阿尔法" not in c["doc_name"] and "阿尔法" not in c["excerpt"], "引用零 A 内容"
    # B 库提问必须正常命中（证明过滤不是"全都搜不到"式假通过）
    r3 = world["member"].post("/api/v1/chat", json={"question": "贝塔 退换货 规则"})
    assert r3.json()["citations"], "命中测试断言：授权库必须可问答"
    assert r3.json()["citations"][0]["doc_name"] == "贝塔.txt", "文件名由 fixture content[:2] 唯一确定"


def test_explicit_unauthorized_kb_ids_403(world):
    _grant(world, "b")
    for url, body in [("/api/v1/chat", {"question": "x"}),
                      ("/api/v1/retrieve", {"query": "x"})]:
        r = world["member"].post(url, json={**body, "kb_ids": [world["ids"]["a"]]})
        assert r.status_code == 403
        assert world["member"].post(url, json={**body, "kb_ids": [world["ids"]["b"]]}).status_code == 200


def test_empty_grants_chat_returns_miss_never_full_corpus(world):
    r = world["member"].post("/api/v1/chat", json={"question": "阿尔法的发货时效"})
    assert r.status_code == 200 and r.json()["citations"] == []
    assert "资料里没有" in r.json()["answer"]


def test_conversations_isolated_per_user(world):
    _grant(world, "b")
    conv = world["member"].post("/api/v1/chat", json={"question": "贝塔 规则"}).json()["conversation_id"]
    assert world["member"].get("/api/v1/conversations").json()[0]["id"] == conv
    assert world["admin"].get("/api/v1/conversations").json() == [], "admin 也没聊过——看不到别人的"
    assert world["admin"].get(f"/api/v1/conversations/{conv}/messages").status_code == 404
    assert world["member"].get(f"/api/v1/conversations/{conv}/messages").status_code == 200


def test_chat_provided_conversation_id_is_no_existence_oracle(world):
    """终审收口①：conversation_id 给了就必须"存在且归调用者"，否则一律同一 404 文案。

    旧实现的洞：只有"存在且跨用户"才 404，"根本不存在的 id"往下走当成新建会话回 200 ——
    于是 (404=别人的会话 / 200=没人用过这个号) 成了存在性探测口，违反 spec §0
    "无权限 == 不存在，不可区分"。只有 conversation_id 缺席（null）才新建。
    """
    _grant(world, "b")
    foreign = world["member"].post("/api/v1/chat", json={"question": "贝塔 规则"}).json()["conversation_id"]
    ghost = world["admin"].post("/api/v1/chat", json={"question": "贝塔 规则"}).json()["conversation_id"] + 10_000
    details = []
    for actor, cid in [(world["admin"], foreign),        # 别人的真实会话
                       (world["member"], ghost),         # 全新的、从未用过的号
                       (world["member"], foreign + 50_000)]:  # 全新的号（换个起点）
        r = actor.post("/api/v1/chat", json={"question": "贝塔 规则", "conversation_id": cid})
        assert r.status_code == 404, f"会话 {cid} 不该回 {r.status_code}（存在性探测口）"
        details.append(r.json()["detail"])
    assert details == ["会话不存在"] * 3, "三种情形必须逐字同文案，不给任何区分信号"
    # 对照：id 缺席才允许新建（否则上面的 404 就成了"chat 永远不可用"式假通过）
    fresh = world["member"].post("/api/v1/chat", json={"question": "贝塔 规则", "conversation_id": None})
    assert fresh.status_code == 200
    assert fresh.json()["conversation_id"] not in (foreign, ghost)


def test_chat_records_real_user(world):
    _grant(world, "b")
    world["member"].post("/api/v1/chat", json={"question": "贝塔 规则"})
    with Session(world["engine"]) as s:
        conv = s.query(Conversation).first()
        usage = s.query(UsageRecord).order_by(UsageRecord.id.desc()).first()
    assert conv.user_email == MEMBER[0]
    assert usage.user_email == MEMBER[0], "端点必记且只记一次，台账归真实登录人"


def test_role_matrix_on_admin_surface(world):
    m = world["member"]
    assert m.post("/api/v1/kb", json={"name": "x"}).status_code == 403
    assert m.get("/api/v1/models").status_code == 403
    assert m.get("/api/v1/usage/summary").status_code == 403
    assert m.get("/api/v1/audit").status_code == 403
    assert m.get("/api/v1/users").status_code == 403
