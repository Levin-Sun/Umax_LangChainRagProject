# 知识库删除（§A 收尾）：整库删除是破坏性最强的管理动作——文档、切块、授权一起消失。
# 与文档删除同类（`kb_deleted` 审计动作同样早已登记未实现），但级联面更广：
#   清：documents / chunks（FK CASCADE）、user_kb_grants（FK CASCADE）、落盘原文件
#   留：usage_records（kb_id 置 NULL——成本台账是事实来源，不随库消失；否则历史账目会凭空少掉）
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import (ApiKey, AuditLog, Chunk, Conversation, Document, KnowledgeBase,
                        UsageRecord, UserKbGrant)
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


def _client(engine, tmp_path):
    return TestClient(create_app(engine=engine, secret="k-secret", embedder=None,
                                 chat_fn=lambda q, h: {"answer": "a[1]", "prompt_tokens": 5,
                                                       "completion_tokens": 2},
                                 upload_dir=str(tmp_path)))


@pytest.fixture
def client(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER, role="member")
    login(c, *ADMIN)
    return c


def _mklib(c, name="待删库", docs=2):
    kb = c.post("/api/v1/kb", json={"name": name}).json()
    for i in range(docs):
        c.post(f"/api/v1/kb/{kb['id']}/documents",
               files={"file": (f"doc{i}.txt", ("售后规则说明。" * 30).encode(), "text/plain")})
    return kb


def _seed_grant_and_usage(engine, kb_id, doc_id):
    from sqlalchemy.orm import Session as S
    with S(engine) as s:
        uid = s.query(__import__("app.models", fromlist=["User"]).User).filter_by(
            email=MEMBER[0]).first().id
        s.add(UserKbGrant(user_id=uid, kb_id=kb_id))
        s.add(UsageRecord(tenant_id="default", user_email=MEMBER[0], kb_id=kb_id,
                          scenario="chat", model="m", prompt_tokens=10, completion_tokens=5))
        s.commit()


def test_delete_kb_cascades_documents_chunks_and_grants(client, engine, tmp_path):
    from pathlib import Path
    kb = _mklib(client, docs=2)
    listed = client.get(f"/api/v1/kb/{kb['id']}/documents").json()
    assert len(listed) == 2
    _seed_grant_and_usage(engine, kb["id"], listed[0]["id"])
    with Session(engine) as s:
        files = [d.storage_path for d in s.query(Document).filter_by(kb_id=kb["id"])]
    assert all(Path(f).exists() for f in files)

    assert client.delete(f"/api/v1/kb/{kb['id']}").status_code == 204

    with Session(engine) as s:
        assert s.get(KnowledgeBase, kb["id"]) is None
        assert s.query(Document).filter_by(kb_id=kb["id"]).count() == 0
        assert s.query(Chunk).filter_by(kb_id=kb["id"]).count() == 0
        assert s.query(UserKbGrant).filter_by(kb_id=kb["id"]).count() == 0
        # 成本台账保留（kb_id 置空而非删行）——历史账目不能凭空少掉
        used = s.query(UsageRecord).filter_by(user_email=MEMBER[0]).all()
        assert len(used) == 1 and used[0].kb_id is None and used[0].prompt_tokens == 10
    assert not any(Path(f).exists() for f in files), "落盘原文件应一并清理"


def test_delete_kb_is_idempotent_and_404s(client):
    kb = _mklib(client, docs=0)
    assert client.delete(f"/api/v1/kb/{kb['id']}").status_code == 204
    assert client.delete(f"/api/v1/kb/{kb['id']}").status_code == 404
    assert client.delete("/api/v1/kb/99999").status_code == 404
    assert client.get("/api/v1/kb").json() == []


def test_delete_kb_requires_admin_and_audits(client, engine, tmp_path):
    kb = _mklib(client, docs=1)
    # 匿名 401
    anon = TestClient(create_app(engine=engine, secret="k-secret", embedder=None,
                                chat_fn=None, upload_dir=str(tmp_path)))
    assert anon.delete(f"/api/v1/kb/{kb['id']}").status_code == 401
    # member 403（授权也删不掉库）
    login(client, *MEMBER)
    assert client.delete(f"/api/v1/kb/{kb['id']}").status_code == 403
    login(client, *ADMIN)
    assert client.delete(f"/api/v1/kb/{kb['id']}").status_code == 204
    with Session(engine) as s:
        rows = s.query(AuditLog).filter_by(action="kb_deleted").all()
    assert len(rows) == 1
    assert rows[0].detail == {"name": kb["name"], "documents": 1}


def test_deleting_kb_leaves_apikey_and_conversations_harmless(client, engine):
    """API key 作用域与历史会话里的 kb_ids 会保留孤儿 id：交集语义使其天然无害，不做事后清理。"""
    kb = _mklib(client, docs=0)
    key = client.post("/api/v1/api-keys", json={"name": "绑定该库", "kb_ids": [kb["id"]]}).json()
    conv = client.post("/api/v1/chat", json={"question": "q", "kb_ids": [kb["id"]]}).json()
    assert client.delete(f"/api/v1/kb/{kb['id']}").status_code == 204
    # key 仍在（作用域成了空集→召回为空），会话历史仍在
    assert any(k["id"] == key["id"] for k in client.get("/api/v1/api-keys").json())
    msgs = client.get(f"/api/v1/conversations/{conv['conversation_id']}/messages").json()
    assert len(msgs) == 2
    with Session(engine) as s:
        assert s.query(ApiKey).filter_by(id=key["id"]).count() == 1
        assert s.query(Conversation).filter_by(id=conv["conversation_id"]).count() == 1


def test_delete_kb_with_pending_documents_does_not_break_worker_path(client, engine, tmp_path):
    """异步入库档：库里还有 pending 文档时删库——worker 随后找不到文档，只回 failed，不炸。"""
    class Q:
        def __init__(self):
            self.ids: list[int] = []

        def enqueue_import(self, doc_id: int) -> None:
            self.ids.append(doc_id)

    q = Q()
    c = TestClient(create_app(engine=engine, secret="k-secret", embedder=None,
                              chat_fn=None, upload_dir=str(tmp_path), queue=q))
    login(c, *ADMIN)
    kb = c.post("/api/v1/kb", json={"name": "异步入库库"}).json()
    c.post(f"/api/v1/kb/{kb['id']}/documents",
           files={"file": ("p.txt", b"content", "text/plain")})
    assert q.ids, "应已入队"
    assert c.delete(f"/api/v1/kb/{kb['id']}").status_code == 204
    from app.worker import run_import
    out = run_import(document_id=q.ids[0], engine=engine)
    assert out["status"] == "failed" and "不存在" in out["error"]
