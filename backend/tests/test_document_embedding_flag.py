# 文档"有没有向量"这个信号。
#
# 客户会真实踩到的顺序是**先传文档、后配向量模型**：那次入库退化成"只建 BM25"（文档照样 ready），
# 界面上看不出来，只能靠人想起来去点重建。所以列表必须能说清"这篇没有向量"。
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import login, seed_user
from tests.test_api import FakeEmbedder

BODY = "售后规则：生鲜商品不支持七天无理由退货，坏果按比例赔偿。" * 5


def _client(engine, tmp_path, *, email, embedder):
    app = create_app(engine=engine, secret="k", embedder=embedder, chat_fn=None,
                     upload_dir=str(tmp_path))
    seed_user(engine, email, "Adm1n-Pass-123", role="admin")
    c = TestClient(app)
    login(c, email, "Adm1n-Pass-123")
    return c


def _upload(c, name):
    kb = c.post("/api/v1/kb", json={"name": name}).json()
    up = c.post(f"/api/v1/kb/{kb['id']}/documents",
                files={"file": ("a.txt", BODY.encode(), "text/plain")})
    assert up.status_code == 201, up.text
    return kb["id"], up.json()


def test_document_flags_whether_vectors_exist(engine, db, tmp_path):
    # 有 embedder：入库即带向量 → True（列表、单查、上传响应三处口径一致）
    c = _client(engine, tmp_path, email="with@x.com", embedder=FakeEmbedder(1024))
    kb_id, doc = _upload(c, "有向量")
    assert doc["has_embedding"] is True
    assert c.get(f"/api/v1/kb/{kb_id}/documents").json()[0]["has_embedding"] is True
    assert c.get(f"/api/v1/documents/{doc['id']}").json()["has_embedding"] is True


def test_document_reports_no_vectors_in_bm25_only_mode(engine, db, tmp_path):
    """没有可用向量模型（embedder=None）时入库仍成功，但必须如实说"没有向量"——
    界面据此给"去配向量模型 / 重建索引"的下一步，而不是让人以为已经配好了。"""
    c = _client(engine, tmp_path, email="plain@x.com", embedder=None)
    kb_id, doc = _upload(c, "只建 BM25")
    assert doc["status"] == "ready"
    assert doc["has_embedding"] is False
    assert c.get(f"/api/v1/kb/{kb_id}/documents").json()[0]["has_embedding"] is False
