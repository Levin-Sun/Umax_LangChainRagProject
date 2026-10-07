# 批量上传（§A「支持拖拽上传…支持批量」）：一次拖一批文件进来，逐文件给出结果。
# 语义：**部分成功**——坏文件（不支持的扩展名/超限）只在它自己那行报错，不拖垮整批；
# 因此响应 201 且带逐项 error，而不是整批 415/400。整体仅两类硬错：库不存在(404)/超批量上限(400)。
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
BATCH_MAX = 20


def _client(engine, tmp_path, queue=None):
    return TestClient(create_app(engine=engine, secret="b-secret", embedder=None,
                                 chat_fn=None, upload_dir=str(tmp_path), queue=queue))


@pytest.fixture
def client(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    c.post("/api/v1/kb", json={"name": "批量库"})
    return c


def _files(*names: str):
    return [("files", (n, f"{n} 的内容：售后规则说明。".encode(), "text/plain")) for n in names]


def _upload(c, kb_id=1, files=None):
    return c.post(f"/api/v1/kb/{kb_id}/documents/batch", files=files or _files("a.txt"))


def test_batch_uploads_all_and_returns_per_file_results(client):
    r = _upload(client, files=_files("第一批.txt", "第二批.md", "第三批.txt"))
    assert r.status_code == 201, r.text
    items = r.json()
    assert [i["name"] for i in items] == ["第一批.txt", "第二批.md", "第三批.txt"]
    assert all(i["error"] is None and i["document"]["status"] == "ready" for i in items)
    listed = client.get("/api/v1/kb/1/documents").json()
    assert [d["name"] for d in listed] == ["第一批.txt", "第二批.md", "第三批.txt"]


def test_batch_partial_success_bad_file_does_not_fail_whole_batch(client):
    files = _files("好的.txt") + [("files", ("坏的.exe", b"MZ", "application/octet-stream"))]
    r = _upload(client, files=files)
    assert r.status_code == 201, "坏文件只在自己那行报错，整批仍是成功"
    items = r.json()
    assert items[0]["error"] is None and items[0]["document"]["status"] == "ready"
    assert items[1]["document"] is None and "不支持" in items[1]["error"]
    assert [d["name"] for d in client.get("/api/v1/kb/1/documents").json()] == ["好的.txt"]


def test_batch_rejects_over_limit_and_unknown_kb(client):
    r = _upload(client, files=_files(*[f"f{i}.txt" for i in range(BATCH_MAX + 1)]))
    assert r.status_code == 400 and str(BATCH_MAX) in r.json()["detail"]
    assert _upload(client, kb_id=999).status_code == 404
    assert client.get("/api/v1/kb/1/documents").json() == []      # 两次都没落库


def test_batch_requires_admin_and_sanitizes_names(client, engine):
    client.post("/api/v1/auth/logout")
    seed_user(engine, "dev@umax.local", "Dev1-Pass-123", role="member")
    login(client, "dev@umax.local", "Dev1-Pass-123")
    assert _upload(client).status_code == 403
    login(client, *ADMIN)
    # 路径注入/NUL 走既有清洗闸（basename + 去 NUL）
    r = _upload(client, files=[("files", ("../../etc/passwd.txt", b"x", "text/plain"))])
    assert r.status_code == 201
    assert r.json()[0]["name"] == "passwd.txt"


def test_batch_enqueues_each_file_when_async(engine, db, tmp_path):
    calls: list[int] = []

    class Q:
        def enqueue_import(self, doc_id: int) -> None:
            calls.append(doc_id)

    c = _client(engine, tmp_path, queue=Q())
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    c.post("/api/v1/kb", json={"name": "库"})
    r = _upload(c, files=_files("a.txt", "b.txt"))
    assert r.status_code == 201
    assert all(i["document"]["status"] == "pending" for i in r.json())
    assert sorted(calls) == [1, 2]      # 每个文档各入队一次
