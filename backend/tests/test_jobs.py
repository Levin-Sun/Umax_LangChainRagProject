# 后台作业记录 + 重启收尾（评审遗留：进程被杀时"跑完没有"必须答得出来）。
#
# 此前只有评测有记录（eval_runs），重建索引没有：客户点一次重建、容器重启，
# 界面上既没有作业也没有结论，只能靠"文档列表里还剩几个排队中"去猜。
# 作业行记录"谁、何时、对什么范围、发起了什么"；**进度仍就地取文档状态机**（不另造一套）。
#
# 更要紧的是**重启收尾**：同步档的后台线程随进程一起没了，卡在 pending/parsing 的文档
# 永远显示"排队中"而没有任何东西会去处理它——这正是评审里那个失败形态。
# 但异步档（ARQ）的 pending 文档由**独立 worker** 消费，进程重启不影响它，**绝不能动**。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import BackgroundJob, Chunk, Document, KnowledgeBase
from app.services.jobs import recover_interrupted_jobs
from tests.conftest import login, seed_user
from tests.test_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
BODY = "售后处理流程说明：客户申请退货先核对订单号。" * 10


class RecordingQueue:
    def __init__(self):
        self.enqueued: list[int] = []

    def enqueue_import(self, document_id: int, job_id: int | None = None) -> None:
        self.enqueued.append(document_id)


def _seed_admin(engine) -> None:
    from app.models import User
    with Session(engine) as s:
        if s.query(User).filter_by(email=ADMIN[0]).first() is None:
            seed_user(engine, *ADMIN, role="admin")


def _client(engine, tmp_path, *, queue=None):
    c = TestClient(create_app(engine=engine, secret="k", embedder=FakeEmbedder(1024),
                              chat_fn=None, upload_dir=str(tmp_path), queue=queue,
                              spawn=lambda fn: fn()))
    _seed_admin(engine)
    login(c, *ADMIN)
    return c


def _kb_with_docs(c, name="作业库", docs=2) -> int:
    kb = c.post("/api/v1/kb", json={"name": name}).json()
    for i in range(docs):
        c.post(f"/api/v1/kb/{kb['id']}/documents",
               files={"file": (f"{name}-{i}.txt", BODY.encode(), "text/plain")})
    return kb["id"]


# ---- 同步档：作业记录与进度 ----
def test_reindex_records_a_job_with_scope_owner_and_progress(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    kb_id = _kb_with_docs(c, docs=3)
    out = c.post("/api/v1/reindex", json={"kb_ids": [kb_id]}).json()
    assert out["documents"] == 3 and out["job_id"], "重建必须留下作业号（否则重启后无从查起）"

    jobs = c.get("/api/v1/jobs").json()
    assert [j["id"] for j in jobs] == [out["job_id"]]        # 最近在前
    job = jobs[0]
    assert job["kind"] == "reindex" and job["status"] == "done"
    assert job["scope"] == {"kb_ids": [kb_id]}
    assert (job["total"], job["done"], job["failed"]) == (3, 3, 0)
    assert job["created_by"] == ADMIN[0]
    assert job["finished_at"] and job["error"] is None


def test_job_counts_partial_failures_but_still_finishes(engine, db, tmp_path):
    """单篇失败只记 failed 计数、作业照常收尾（'部分成功'与评测/批量上传同口径）。"""
    c = _client(engine, tmp_path)
    kb_id = _kb_with_docs(c, docs=2)
    with Session(engine) as s:      # 让其中一篇的原文件消失
        doc = s.query(Document).filter_by(kb_id=kb_id).order_by(Document.id).first()
        doc.storage_path = str(tmp_path / "不存在.txt")
        s.commit()
    out = c.post("/api/v1/reindex", json={"kb_ids": [kb_id]}).json()
    job = next(j for j in c.get("/api/v1/jobs").json() if j["id"] == out["job_id"])
    assert job["status"] == "done"
    assert (job["total"], job["done"], job["failed"]) == (2, 1, 1)


def test_reindex_all_libraries_job_scope_is_null(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    _kb_with_docs(c, name="库A", docs=1)
    _kb_with_docs(c, name="库B", docs=1)
    out = c.post("/api/v1/reindex", json={}).json()
    assert out["job_id"]
    job = next(j for j in c.get("/api/v1/jobs").json() if j["id"] == out["job_id"])
    assert job["scope"] == {"kb_ids": None} and job["total"] == 2


# ---- 异步档：作业只登记意图，文档交给 worker（进度看文档列表）----
def test_arq_mode_records_queued_job_and_leaves_docs_to_worker(engine, db, tmp_path):
    q = RecordingQueue()
    c = _client(engine, tmp_path, queue=q)
    kb_id = _kb_with_docs(c, docs=2)      # 上传本身也会入队（异步档）
    q.enqueued.clear()
    out = c.post("/api/v1/reindex", json={"kb_ids": [kb_id]}).json()
    job = next(j for j in c.get("/api/v1/jobs").json() if j["id"] == out["job_id"])
    assert job["status"] == "queued", "异步档由 worker 负责，作业状态不该谎称 running"
    assert len(q.enqueued) == 2           # 逐篇交给 worker
    with Session(engine) as s:
        assert all(d.status == "pending" for d in s.query(Document).filter_by(kb_id=kb_id))


# ---- 重启收尾：这是本功能真正的价值 ----
def test_recovery_closes_interrupted_jobs_and_orphan_docs_in_sync_mode(engine, db):
    """同步档：进程被杀 → 后台线程没了，卡在 pending/parsing 的文档永远"排队中"。"""
    with Session(engine) as s:
        kb = KnowledgeBase(tenant_id="default", name="重启库")
        s.add(kb); s.flush()
        s.add(BackgroundJob(tenant_id="default", kind="reindex", status="running",
                            scope={"kb_ids": [kb.id]}, total=3, done=1, created_by="a@b.c"))
        for i, st in enumerate(("ready", "pending", "parsing")):
            s.add(Document(tenant_id="default", kb_id=kb.id, name=f"d{i}.txt", status=st,
                           storage_path="/tmp/x"))
        s.commit()
        kb_id = kb.id
    out = recover_interrupted_jobs(engine, has_external_worker=False)
    assert out == {"jobs": 1, "documents": 2}
    with Session(engine) as s:
        job = s.query(BackgroundJob).one()
        assert job.status == "interrupted" and job.finished_at is not None
        assert "重启" in (job.error or "")
        docs = {d.name: d for d in s.query(Document).filter_by(kb_id=kb_id)}
    assert docs["d0.txt"].status == "ready"            # 已完成的文档不动
    assert docs["d1.txt"].status == "failed" and "中断" in docs["d1.txt"].error
    assert docs["d2.txt"].status == "failed"           # parsing 中被打断的也要收尾


def test_recovery_never_touches_docs_in_arq_mode(engine, db):
    """异步档：pending 文档由独立 worker 消费，进程重启不影响它——误标会让排队中的文档变失败。"""
    with Session(engine) as s:
        kb = KnowledgeBase(tenant_id="default", name="异步库")
        s.add(kb); s.flush()
        s.add(BackgroundJob(tenant_id="default", kind="reindex", status="queued",
                            scope={"kb_ids": [kb.id]}, total=1, created_by="a@b.c"))
        s.add(BackgroundJob(tenant_id="default", kind="reindex", status="running",
                            scope={"kb_ids": [kb.id]}, total=1, created_by="a@b.c"))
        s.add(Document(tenant_id="default", kb_id=kb.id, name="queued.txt", status="pending",
                       storage_path="/tmp/x"))
        s.commit()
    out = recover_interrupted_jobs(engine, has_external_worker=True)
    assert out == {"jobs": 1, "documents": 0}, "只标 running 的作业，绝不动文档"
    with Session(engine) as s:
        statuses = sorted(j.status for j in s.query(BackgroundJob))
        assert statuses == ["interrupted", "queued"], "queued 的作业仍归 worker，不该改"
        assert s.query(Document).one().status == "pending"


def test_jobs_list_requires_admin(engine, db, tmp_path):
    anon = TestClient(create_app(engine=engine, secret="k", embedder=None, chat_fn=None,
                                 upload_dir=str(tmp_path)))
    assert anon.get("/api/v1/jobs").status_code == 401
    c = _client(engine, tmp_path)
    seed_user(engine, email="m@umax.local", password="Member-Pass-1", role="member")
    login(c, "m@umax.local", "Member-Pass-1")
    assert c.get("/api/v1/jobs").status_code == 403


# ---- 异步档：作业的收尾只能由 worker 回报（此前漏了这一步）----
class JobAwareQueue:
    """记录 (doc_id, job_id)：异步档的作业号必须随任务一起下发给 worker。"""

    def __init__(self):
        self.pairs: list[tuple[int, int | None]] = []

    def enqueue_import(self, document_id: int, job_id: int | None = None) -> None:
        self.pairs.append((document_id, job_id))


def _run_all_pending(engine, pairs: list[tuple[int, int | None]], job_id: int) -> None:
    """模拟真 worker 逐个接手（真环境里是 arq 进程，这里直接调同一个函数）。"""
    from app.worker import run_import

    for doc_id, jid in pairs:
        assert jid == job_id, "作业号没随任务下发，worker 就无从回报进度"
        run_import(document_id=doc_id, engine=engine, embedder=FakeEmbedder(1024), job_id=job_id)


def test_async_reindex_job_is_finished_by_worker_reports(engine, db, tmp_path):
    """异步档点重建：backend 只建作业行 + 入队，**终局由 worker 每篇回报一次**。

    此前漏了回报这一步——作业行建完永远停在 queued、done/failed 恒为 0，界面"排队中"
    永不结束（而文档其实已经 ready，只有作业行在撒谎）。交付验证（arq 档）会直接撞见它。
    """
    q = JobAwareQueue()
    c = _client(engine, tmp_path, queue=q)
    kb_id = _kb_with_docs(c, docs=2)
    q.pairs.clear()                     # 上传本身也会入队，这里只看重建这一段
    out = c.post("/api/v1/reindex", json={"kb_ids": [kb_id]}).json()
    job_id = out["job_id"]
    assert len(q.pairs) == 2            # 每篇都入队，且带同一个作业号
    with Session(engine) as s:
        job = s.get(BackgroundJob, job_id)
        assert (job.status, job.done, job.failed) == ("queued", 0, 0)
    _run_all_pending(engine, q.pairs, job_id)
    with Session(engine) as s:
        job = s.get(BackgroundJob, job_id)
        assert (job.status, job.done, job.failed) == ("done", 2, 0)
        assert job.finished_at is not None
        assert s.query(Document).filter_by(kb_id=kb_id).filter_by(status="ready").count() == 2


def test_async_job_reports_partial_failures_like_the_sync_path(engine, db, tmp_path):
    """一篇坏文件不该让作业收不了尾：计数照实、终局照旧（与同步档同一套 finish_job 语义）。"""
    q = JobAwareQueue()
    c = _client(engine, tmp_path, queue=q)
    kb_id = _kb_with_docs(c, docs=2)
    q.pairs.clear()
    job_id = c.post("/api/v1/reindex", json={"kb_ids": [kb_id]}).json()["job_id"]
    with Session(engine) as s:          # 原始文件丢了（磁盘被清过）
        bad = s.query(Document).filter_by(kb_id=kb_id).order_by(Document.id).first()
        bad.storage_path = str(tmp_path / "没了.txt")
        s.commit()
    _run_all_pending(engine, q.pairs, job_id)
    with Session(engine) as s:
        job = s.get(BackgroundJob, job_id)
        assert (job.status, job.done, job.failed) == ("done", 1, 1)


def test_async_job_finishes_when_a_queued_document_was_deleted(engine, db, tmp_path):
    """排队期间被删的那一篇也要计入（否则作业永远差一篇、界面永远不结束）。"""
    q = JobAwareQueue()
    c = _client(engine, tmp_path, queue=q)
    kb_id = _kb_with_docs(c, docs=2)
    q.pairs.clear()
    job_id = c.post("/api/v1/reindex", json={"kb_ids": [kb_id]}).json()["job_id"]
    with Session(engine) as s:
        gone = s.query(Document).filter_by(kb_id=kb_id).order_by(Document.id).first()
        gone_id = gone.id
        s.delete(gone)
        s.commit()
    _run_all_pending(engine, q.pairs, job_id)      # 包含已删的那篇 id
    with Session(engine) as s:
        job = s.get(BackgroundJob, job_id)
        assert (job.status, job.done, job.failed) == ("done", 1, 1)
        assert s.get(Document, gone_id) is None
