# 跨部署模式一致性：同一份文档，同步档与异步档必须切出**同样的块**。
#
# 评审发现（真机复现）：同步端点走 `_chunk_params`（建库锁定值优先、其次配置中心生效值），
# 而 `worker.run_import` 直接用 `.env` 的 `chunk_target` —— 于是 QUEUE_BACKEND 从 sync 换成 arq，
# 同一篇文档切块数就变了（实测 1 块 vs 3 块），文档承诺的"建库时锁定切分参数"在异步档失效。
# 这类"同一语义有多条实现"的漂移不会自己显形，必须用测试钉住。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AppSetting, Chunk, Document
from app.worker import run_import
from tests.conftest import login, seed_user
from tests.test_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
TEXT = "售后处理流程说明：客户申请退货先核对订单号，破损优先补发。" * 40   # 够长，参数不同必异


class RecordingQueue:
    def __init__(self):
        self.enqueued: list[int] = []

    def enqueue_import(self, document_id: int, job_id: int | None = None) -> None:
        self.enqueued.append(document_id)


def _seed_admin(engine) -> None:
    """同一个用例里要开两个客户端（同步档/异步档），播种必须幂等。"""
    from app.models import User
    with Session(engine) as s:
        if s.query(User).filter_by(email=ADMIN[0]).first() is None:
            seed_user(engine, *ADMIN, role="admin")


def _client(engine, tmp_path, embedder, queue=None):
    c = TestClient(create_app(engine=engine, secret="k", embedder=embedder, chat_fn=None,
                              upload_dir=str(tmp_path), queue=queue,
                              spawn=lambda fn: fn()))
    _seed_admin(engine)
    login(c, *ADMIN)
    return c


def _chunks(engine, doc_id: int) -> list[str]:
    with Session(engine) as s:
        return [c.content for c in s.query(Chunk).filter_by(document_id=doc_id)
                .order_by(Chunk.chunk_index)]


def test_sync_and_async_ingest_chunk_identically(engine, db, tmp_path):
    """建库锁定 1000 → 配置中心改回 300 → 两种模式仍须切出同一份结果。

    锁定值优先是文档写明的语义（§3.3），异步档必须和同步档一样遵守——
    否则"同一客户两种部署方式，检索质量与评测基线不同"，而且没人会发现。
    """
    emb = FakeEmbedder(1024)
    with Session(engine) as s:      # 配置中心：先设 1000，建库时会把它锁进 KB
        s.add(AppSetting(key="chunk_target", value=1000, updated_by="probe"))
        s.commit()
    ck = _client(engine, tmp_path, emb)                    # 同步档
    kb = ck.post("/api/v1/kb", json={"name": "一致性库"}).json()
    assert kb["id"]
    with Session(engine) as s:      # 建库后把全局配置改回 300：此后只有"建库锁定值"该说话
        row = s.get(AppSetting, "chunk_target")
        row.value = 300
        s.commit()

    doc_sync = ck.post(f"/api/v1/kb/{kb['id']}/documents",
                       files={"file": ("sync.txt", TEXT.encode(), "text/plain")}).json()
    assert doc_sync["status"] == "ready"

    q = RecordingQueue()
    ca = _client(engine, tmp_path, emb, queue=q)            # 异步档
    doc_async = ca.post(f"/api/v1/kb/{kb['id']}/documents",
                        files={"file": ("async.txt", TEXT.encode(), "text/plain")}).json()
    assert doc_async["status"] == "pending" and q.enqueued == [doc_async["id"]]
    assert run_import(document_id=doc_async["id"], engine=engine, embedder=emb)["status"] == "ready"

    sync_chunks = _chunks(engine, doc_sync["id"])
    async_chunks = _chunks(engine, doc_async["id"])
    assert sync_chunks == async_chunks, (
        f"两种部署模式切块不一致：同步 {len(sync_chunks)} 块 / 异步 {len(async_chunks)} 块")


def test_async_ingest_honours_runtime_config_center(engine, db, tmp_path):
    """没有建库锁定值时（老库 chunk_target 为空），异步档也必须读配置中心的生效值。"""
    emb = FakeEmbedder(1024)
    with Session(engine) as s:
        s.add(AppSetting(key="chunk_target", value=120, updated_by="probe"))
        s.commit()
    kb = {"id": None}
    ck = _client(engine, tmp_path, emb)
    kb["id"] = ck.post("/api/v1/kb", json={"name": "老库模拟"}).json()["id"]
    with Session(engine) as s:      # 模拟"建库时没锁"的老库
        from app.models import KnowledgeBase
        s.get(KnowledgeBase, kb["id"]).chunk_target = None
        s.commit()
    q = RecordingQueue()
    ca = _client(engine, tmp_path, emb, queue=q)
    doc = ca.post(f"/api/v1/kb/{kb['id']}/documents",
                  files={"file": ("c.txt", TEXT.encode(), "text/plain")}).json()
    run_import(document_id=doc["id"], engine=engine, embedder=emb)
    with Session(engine) as s:
        assert s.get(Document, doc["id"]).status == "ready"
        n = s.query(Chunk).filter_by(document_id=doc["id"]).count()
    assert n > 1, f"配置中心设了 120 字，异步档却切出 {n} 块（说明它没读配置中心）"
