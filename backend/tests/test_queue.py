# TDD 红灯：ARQ 异步入库流水线（队列抽象注入，默认同步保持任务4语义）
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.worker import run_import
from tests.test_api import FakeEmbedder


class RecordingQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue_import(self, document_id: int):
        self.enqueued.append(document_id)


@pytest.fixture
def embedder():
    from app.core.config import get_settings
    return FakeEmbedder(get_settings().embedding_dim)


def _client(engine, tmp_path, embedder, queue=None):
    app = create_app(engine=engine, embedder=embedder,
                     chat_fn=lambda q, h: {"answer": "x", "prompt_tokens": 0,
                                           "completion_tokens": 0},
                     upload_dir=str(tmp_path), queue=queue)
    return TestClient(app)


def test_upload_with_queue_returns_pending_and_enqueues(engine, db, tmp_path, embedder):
    q = RecordingQueue()
    client = _client(engine, tmp_path, embedder, queue=q)
    kb = client.post("/api/kb", json={"name": "k"}).json()
    doc = client.post(f"/api/kb/{kb['id']}/documents",
                      files={"file": ("a.txt", "生鲜退货 政策".encode(), "text/plain")}).json()
    assert doc["status"] == "pending"
    assert q.enqueued == [doc["id"]]
    assert client.get(f"/api/documents/{doc['id']}/chunks").json() == []


def test_worker_run_import_completes_pipeline(engine, db, tmp_path, embedder):
    client = _client(engine, tmp_path, embedder)  # 同步模式先落盘
    kb = client.post("/api/kb", json={"name": "k"}).json()
    doc = client.post(f"/api/kb/{kb['id']}/documents",
                      files={"file": ("b.txt", "干线物流 时效".encode(), "text/plain")}).json()
    from app.models import Document

    with Session(engine) as s:
        d = s.get(Document, doc["id"])
        d.status = "pending"  # 模拟队列领取前状态
        s.commit()
    result = run_import(document_id=doc["id"], engine=engine, embedder=embedder)
    assert result["status"] == "ready"
    assert len(client.get(f"/api/documents/{doc['id']}/chunks").json()) == 1


def test_worker_marks_failed_with_reason(engine, db, tmp_path, embedder):
    from app.models import Document

    p = tmp_path / "broken.docx"
    p.write_bytes(b"not a real docx")
    with Session(engine) as s:
        from app.models import KnowledgeBase

        kb = KnowledgeBase(tenant_id="default", name="k")
        s.add(kb)
        s.flush()
        d = Document(tenant_id="default", kb_id=kb.id, name="broken.docx",
                     status="pending", storage_path=str(p))
        s.add(d)
        s.commit()
        doc_id = d.id
    result = run_import(document_id=doc_id, engine=engine, embedder=embedder)
    assert result["status"] == "failed"
    assert result["error"], "失败必须记录原因供界面展示"


def test_reprocess_with_queue_enqueues(engine, db, tmp_path, embedder):
    q = RecordingQueue()
    client = _client(engine, tmp_path, embedder, queue=q)
    kb = client.post("/api/kb", json={"name": "k"}).json()
    doc = client.post(f"/api/kb/{kb['id']}/documents",
                      files={"file": ("c.txt", b"x y z", "text/plain")}).json()
    q.enqueued.clear()
    r = client.post(f"/api/documents/{doc['id']}/reprocess")
    assert r.status_code == 202 and r.json()["status"] == "pending"
    assert q.enqueued == [doc["id"]]
