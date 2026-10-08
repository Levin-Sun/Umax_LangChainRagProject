# 异步入库流水线（队列抽象注入，默认同步保持任务4语义）
# 阶段 2 起 kb 写/文档写是 admin 面：夹具客户端统一登录 admin
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.worker import run_import
from tests.conftest import login, seed_user
from tests.test_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


class RecordingQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue_import(self, document_id: int, job_id: int | None = None):
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
    c = TestClient(app)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    return c


def test_upload_with_queue_returns_pending_and_enqueues(engine, db, tmp_path, embedder):
    q = RecordingQueue()
    client = _client(engine, tmp_path, embedder, queue=q)
    kb = client.post("/api/v1/kb", json={"name": "k"}).json()
    doc = client.post(f"/api/v1/kb/{kb['id']}/documents",
                      files={"file": ("a.txt", "生鲜退货 政策".encode(), "text/plain")}).json()
    assert doc["status"] == "pending"
    assert q.enqueued == [doc["id"]]
    assert client.get(f"/api/v1/documents/{doc['id']}/chunks").json() == []


def test_worker_run_import_completes_pipeline(engine, db, tmp_path, embedder):
    client = _client(engine, tmp_path, embedder)  # 同步模式先落盘
    kb = client.post("/api/v1/kb", json={"name": "k"}).json()
    doc = client.post(f"/api/v1/kb/{kb['id']}/documents",
                      files={"file": ("b.txt", "干线物流 时效".encode(), "text/plain")}).json()
    from app.models import Document

    with Session(engine) as s:
        d = s.get(Document, doc["id"])
        d.status = "pending"  # 模拟队列领取前状态
        s.commit()
    result = run_import(document_id=doc["id"], engine=engine, embedder=embedder)
    assert result["status"] == "ready"
    assert len(client.get(f"/api/v1/documents/{doc['id']}/chunks").json()) == 1


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
    kb = client.post("/api/v1/kb", json={"name": "k"}).json()
    doc = client.post(f"/api/v1/kb/{kb['id']}/documents",
                      files={"file": ("c.txt", b"x y z", "text/plain")}).json()
    q.enqueued.clear()
    r = client.post(f"/api/v1/documents/{doc['id']}/reprocess")
    assert r.status_code == 202 and r.json()["status"] == "pending"
    assert q.enqueued == [doc["id"]]


# ---- ARQ 适配器本身：以前一行都没被执行过（它需要真 Redis），而它是异步档的必经之路 ----
def test_arq_enqueue_uses_the_same_job_name_the_worker_registers(monkeypatch):
    """入队名必须与 worker 注册名**同源**，且作业号随任务下发。

    为什么值得单独钉住：arq 找不到同名函数就**静默丢弃**任务（不报错、不重试、不重投），
    表现是"接口返回 202、文档永远 pending"——最像"偶发故障"的一类，交付验证时才会撞见。
    原先入队侧写字面量 `"import_document"`、worker 侧靠 `__qualname__`，两处独立；
    现在入队侧 import 同一个常量，这条测试用**假 pool**把真实入队路径跑一遍，
    并用 arq 自己的命名逻辑（`arq.worker.func`）断言两侧一致——不靠我对 arq 默认行为的假设。
    """
    import arq
    from arq.worker import func as arq_func

    from app.queue import ArqQueue
    from app.worker import IMPORT_JOB_NAME, WorkerSettings, import_document

    calls: list[tuple] = []
    seen: dict = {}

    class FakePool:
        async def enqueue_job(self, name, *args):
            calls.append((name, args))

        async def aclose(self):
            calls.append(("closed",))

    async def fake_create_pool(settings):
        seen["host"], seen["port"] = settings.host, settings.port
        return FakePool()

    monkeypatch.setattr(arq, "create_pool", fake_create_pool)
    ArqQueue(redis_host="redis.internal", redis_port=6390).enqueue_import(77, 12)

    assert calls == [(IMPORT_JOB_NAME, (77, 12)), ("closed",)]   # 顺便：用完要把池还回去
    assert seen == {"host": "redis.internal", "port": 6390}     # Redis 地址走配置
    # 真正的不变量：arq 为"worker 注册的那个函数"推出来的 job 名 == 入队用的名字
    assert arq_func(import_document).name == IMPORT_JOB_NAME
    assert import_document in WorkerSettings.functions


def test_queue_module_has_no_hardcoded_job_name():
    """入队侧不许再出现第二处字面量（这条守卫是"同源"的另一半）。

    上面那条测试证明**当前**两侧一致；这条防止以后有人图省事把字面量写回来——
    那时两条字面量又各自演化，测试却仍然是绿的。
    """
    import inspect

    from app import queue as queue_mod

    src = inspect.getsource(queue_mod)
    assert '"import_document"' not in src and "'import_document'" not in src
    assert "IMPORT_JOB_NAME" in src
