# 原文件不可读时的失败态：文档必须留下"为什么失败"，而不是永远卡在「排队中」。
#
# 评审发现：`read_stored(doc)` 在 ingest 的 try 之外，于是原文件被删/磁盘满/被运维清理时——
# 同步档是未声明的 500、异步档是任务失败重试——而**文档行永远停在 pending**。
# 界面上一直显示"排队中"，没有任何线索指向"原文件没了"；而失败态本身就是排查信息。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import Document, KnowledgeBase
from app.worker import run_import
from tests.conftest import login, seed_user
from tests.test_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


def _client(engine, tmp_path, *, queue=None):
    c = TestClient(create_app(engine=engine, secret="k", embedder=FakeEmbedder(1024),
                              chat_fn=None, upload_dir=str(tmp_path), queue=queue,
                              spawn=lambda fn: fn()))
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    return c


def _kb_with_missing_file(engine, tmp_path) -> tuple[int, int]:
    """一篇"记录存在、原文件已不在"的文档——运维清过 uploads、磁盘迁移、误删都会这样。"""
    with Session(engine) as s:
        kb = KnowledgeBase(tenant_id="default", name="库")
        s.add(kb); s.flush()
        doc = Document(tenant_id="default", kb_id=kb.id, name="gone.txt", status="ready",
                       storage_path=str(tmp_path / "已经不存在了.txt"), size_bytes=10)
        s.add(doc); s.commit()
        return kb.id, doc.id


def test_reprocess_missing_file_leaves_a_failure_state_not_a_500(engine, db, tmp_path):
    kb_id, doc_id = _kb_with_missing_file(engine, tmp_path)
    c = _client(engine, tmp_path)
    r = c.post(f"/api/v1/documents/{doc_id}/reprocess")
    assert r.status_code in (200, 202), f"原文件缺失不该 500：{r.status_code} {r.text[:200]}"
    with Session(engine) as s:
        doc = s.get(Document, doc_id)
    assert doc.status == "failed", "文档必须留下失败态，不能继续停在就绪/排队"
    assert "原文件" in (doc.error or ""), f"失败原因要指向原文件缺失，实得：{doc.error!r}"


def test_worker_missing_file_marks_failed(engine, db, tmp_path):
    """异步档同理（评审前这里是：任务失败重试 + 文档永远 pending）。"""
    kb_id, doc_id = _kb_with_missing_file(engine, tmp_path)
    out = run_import(document_id=doc_id, engine=engine, embedder=FakeEmbedder(1024))
    assert out["status"] == "failed" and "原文件" in out["error"]
    with Session(engine) as s:
        assert s.get(Document, doc_id).status == "failed"


def test_batch_upload_isolates_an_unreadable_file(engine, db, tmp_path, monkeypatch):
    """批量上传的部分成功语义不能被"读文件失败"打破：一个坏文件不该让整批 500。"""
    import app.main as main_mod

    real_read = main_mod.read_stored

    def flaky_read(doc):
        if "bad" in doc.name:
            raise OSError("磁盘 I/O 错误（模拟）")
        return real_read(doc)

    monkeypatch.setattr(main_mod, "read_stored", flaky_read)
    c = _client(engine, tmp_path)
    kb = c.post("/api/v1/kb", json={"name": "库"}).json()
    r = c.post(f"/api/v1/kb/{kb['id']}/documents/batch",
               files=[("files", ("good.txt", b"hello world", "text/plain")),
                      ("files", ("bad.txt", b"hello world", "text/plain"))])
    assert r.status_code == 201, f"整批不该因一个坏文件失败：{r.status_code} {r.text[:200]}"
    items = {i["name"]: i for i in r.json()}
    assert items["bad.txt"]["document"]["status"] == "failed"
    assert "原文件" in items["bad.txt"]["document"]["error"]
    assert items["good.txt"]["document"]["status"] == "ready"     # 其余照常入库
