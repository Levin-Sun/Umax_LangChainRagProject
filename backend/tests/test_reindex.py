# 一键重建索引（§3.3 风险对策「换 embedding 模型翻车」）：
# 换模型后旧向量与新查询不可比，全库必须从头算一遍。客户"先传资料、后买 key"或中途换型
# 都会撞上这件事——一篇篇点「重试」不现实，所以这必须是后台一键任务 + 进度可看。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AuditLog, Chunk, Document, KnowledgeBase
from tests.conftest import login, seed_user
from tests.test_models_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")
BODY = "售后处理流程说明：客户申请退货先核对订单号，破损优先补发。" * 3


def _sync_spawn(fn):
    """测试注入：后台任务同步执行——"跑完再看结果"必须确定性，不靠 sleep 赌时序。"""
    fn()


def _client(engine, tmp_path, *, embedder=None, spawn=_sync_spawn, queue=None):
    return TestClient(create_app(engine=engine, secret="k-secret", embedder=embedder,
                                 chat_fn=None, upload_dir=str(tmp_path), spawn=spawn,
                                 queue=queue))


@pytest.fixture
def client(engine, db, tmp_path):
    c = _client(engine, tmp_path, embedder=FakeEmbedder(1024))
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER, role="member")
    login(c, *ADMIN)
    return c


def _mk_kb(c, name, docs=2):
    kb = c.post("/api/v1/kb", json={"name": name}).json()
    for i in range(docs):
        c.post(f"/api/v1/kb/{kb['id']}/documents",
               files={"file": (f"{name}-{i}.txt", BODY.encode(), "text/plain")})
    return kb


def test_reindex_kb_scope_rebuilds_vectors(engine, db, tmp_path):
    """先造出"没有向量"的库（等价于客户 key 之前入库），再一次重建让它全部带上向量。"""
    c = _client(engine, tmp_path, embedder=None)      # 无 embedding：入库即 BM25-only（真的没向量）
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb(c, "待重建库", docs=3)
    other = _mk_kb(c, "别的库", docs=1)
    with Session(engine) as s:
        assert s.query(Chunk).filter(Chunk.embedding.is_(None)).count() == 4   # 全都没向量

    # 装上 embedding 模型（等价于客户后来配了 key），重建这一个库
    c2 = _client(engine, tmp_path, embedder=FakeEmbedder(1024))
    login(c2, *ADMIN)
    r = c2.post("/api/v1/reindex", json={"kb_ids": [kb["id"]]})
    assert r.status_code == 202, r.text
    body = r.json()
    assert (body["documents"], body["kb_ids"]) == (3, [kb["id"]])
    assert body["job_id"], "重建必须回作业号（进程被杀后靠它查）"

    with Session(engine) as s:
        assert s.query(Chunk).filter_by(kb_id=kb["id"]).count() == 3
        assert s.query(Chunk).filter(Chunk.kb_id == kb["id"],
                                     Chunk.embedding.is_(None)).count() == 0
        # 范围外不动：别的库仍是无向量状态（一键重建不该"顺手"改别的库）
        assert s.query(Chunk).filter(Chunk.kb_id == other["id"],
                                     Chunk.embedding.is_(None)).count() == 1
        assert all(d.status == "ready" for d in s.query(Document).filter_by(kb_id=kb["id"]))
    with Session(engine) as s:
        row = s.query(AuditLog).filter_by(action="reindex_started").one()
        assert row.detail["kb_ids"] == [kb["id"]] and row.detail["documents"] == 3
        assert row.detail["job_id"], "审计里带上作业号：从审计能追到那一次重建"
        assert row.target_type == "kb" and row.target_id == kb["id"]


def test_reindex_all_libraries_when_scope_is_null(engine, db, tmp_path):
    c = _client(engine, tmp_path, embedder=FakeEmbedder(1024))
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    a = _mk_kb(c, "库A", docs=2)
    b = _mk_kb(c, "库B", docs=1)
    r = c.post("/api/v1/reindex", json={})
    assert r.status_code == 202
    assert (r.json()["documents"], r.json()["kb_ids"]) == (3, None) and r.json()["job_id"]
    with Session(engine) as s:
        assert s.query(Chunk).filter(Chunk.embedding.is_(None)).count() == 0
        detail = s.query(AuditLog).filter_by(action="reindex_started").one().detail
        assert (detail["kb_ids"], detail["documents"]) == (None, 3) and detail["job_id"]
    assert {a["id"], b["id"]} == {k["id"] for k in c.get("/api/v1/kb").json()}


def test_reindex_validates_scope_and_empty_state(client):
    assert client.post("/api/v1/reindex", json={"kb_ids": [99999]}).status_code == 400
    assert client.post("/api/v1/reindex", json={}).status_code == 400     # 一个文档都没有
    # 空库（存在但没有文档）同样 400：给客户的是"没事可做"的明确话，而不是一次空转
    empty = client.post("/api/v1/kb", json={"name": "空库"}).json()
    assert client.post("/api/v1/reindex", json={"kb_ids": [empty["id"]]}).status_code == 400


def test_reindex_marks_all_docs_pending_before_background_work(engine, db, tmp_path):
    """①接口返回时状态已翻成 pending —— 前端轮询立刻看到"排队中"，不会把正在重建的库
    误显示成"就绪"（进度可见性是这功能一半的价值）；②异步档全部交给 worker，不由接口线程代劳。"""
    class Recorder:
        def __init__(self):
            self.ids: list[int] = []

        def enqueue_import(self, doc_id: int) -> None:
            self.ids.append(doc_id)

    q = Recorder()
    c = _client(engine, tmp_path, embedder=FakeEmbedder(1024), queue=q)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb(c, "排队库", docs=2)
    q.ids.clear()                            # 上传本身也入队（异步档），这里只看重建这一段
    assert c.post("/api/v1/reindex", json={"kb_ids": [kb["id"]]}).status_code == 202
    with Session(engine) as s:
        ids = [d.id for d in s.query(Document).filter_by(kb_id=kb["id"])]
        assert all(d.status == "pending" for d in s.query(Document).filter_by(kb_id=kb["id"]))
    assert sorted(q.ids) == sorted(ids)     # 每篇都入了队，等 worker 接手


def test_reindex_isolates_a_broken_document(engine, db, tmp_path):
    """单篇坏文件只记在那篇身上：一篇读不出来不该让整库重建停在中途（同批量上传口径）。"""
    c = _client(engine, tmp_path, embedder=FakeEmbedder(1024))
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb(c, "含坏文件库", docs=2)
    with Session(engine) as s:
        bad = s.query(Document).filter_by(kb_id=kb["id"]).order_by(Document.id).first()
        bad.storage_path = str(tmp_path / "不存在.txt")     # 原始文件丢了（磁盘被清过）
        s.commit()
    assert c.post("/api/v1/reindex", json={"kb_ids": [kb["id"]]}).status_code == 202
    with Session(engine) as s:
        rows = s.query(Document).filter_by(kb_id=kb["id"]).order_by(Document.id).all()
    assert rows[0].status == "failed" and "No such file" in (rows[0].error or "")
    assert rows[1].status == "ready"                        # 后面那篇照常重建


def test_reindex_requires_admin_and_license(engine, db, tmp_path):
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER, role="member")
    with Session(engine) as s:
        s.add(KnowledgeBase(tenant_id="default", name="库"))
        s.commit()
    anon = _client(engine, tmp_path)                       # 匿名 401
    assert anon.post("/api/v1/reindex", json={}).status_code == 401
    c = _client(engine, tmp_path, embedder=FakeEmbedder(1024))
    login(c, *MEMBER)                                      # member 403（重建全库是管理动作）
    assert c.post("/api/v1/reindex", json={}).status_code == 403
