# TDD 红灯：FastAPI 服务层契约（知识库/文档/检索/问答/会话）
# 决策：一期无鉴权（初始化向导与登录在后续任务）；chat/embedder 依赖注入，测试用假实现
import hashlib
import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.main import create_app


class FakeEmbedder:
    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for ch in t:
                v[int(hashlib.md5(ch.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@pytest.fixture
def embedder():
    return FakeEmbedder(get_settings().embedding_dim)


@pytest.fixture
def chat_calls():
    return []


@pytest.fixture
def app_client(engine, db: Session, embedder, chat_calls, tmp_path):
    def fake_chat(query, hits):
        chat_calls.append((query, [h["doc_name"] for h in hits]))
        return {"answer": f"据资料，退货需24小时响应[1]。[2]",
                "prompt_tokens": 100, "completion_tokens": 20}

    app = create_app(engine=engine, embedder=embedder, chat_fn=fake_chat,
                     upload_dir=str(tmp_path))
    with TestClient(app) as c:
        yield c


def _upload(client, name="ops.txt", content=b"fresh return policy content"):
    kb = client.post("/api/v1/kb", json={"name": "运营库"}).json()
    r = client.post(f"/api/v1/kb/{kb['id']}/documents",
                    files={"file": (name, content, "text/plain")})
    return kb, r


def test_kb_create_and_list(app_client):
    r = app_client.post("/api/v1/kb", json={"name": "产品手册库", "description": "SKU规范"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "产品手册库" and body["id"] > 0
    listed = app_client.get("/api/v1/kb").json()
    assert [k["id"] for k in listed] == [body["id"]]


def test_upload_txt_creates_ready_doc_and_chunks(app_client):
    text = "生鲜类商品不支持七天无理由退货。\n坏果按比例赔偿，需拍照上传。"
    kb, r = _upload(app_client, "manual.txt", text.encode())
    assert r.status_code == 201
    doc = r.json()
    assert doc["status"] == "ready" and doc["name"] == "manual.txt"
    chunks = app_client.get(f"/api/v1/documents/{doc['id']}/chunks").json()
    assert len(chunks) == 1 and "生鲜" in chunks[0]["content"]
    assert chunks[0]["has_embedding"] is True


def test_upload_rejects_unsupported_ext(app_client):
    kb = app_client.post("/api/v1/kb", json={"name": "k"}).json()
    r = app_client.post(f"/api/v1/kb/{kb['id']}/documents",
                        files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415


def test_document_status_visible_and_reprocess(app_client):
    kb, doc = _upload(app_client)
    doc = doc.json()
    got = app_client.get(f"/api/v1/documents/{doc['id']}").json()
    assert got["status"] == "ready"
    app_client.patch(f"/api/v1/documents/{doc['id']}", json={"status": "failed",
                                                         "error": "模拟解析失败"})
    r = app_client.post(f"/api/v1/documents/{doc['id']}/reprocess")
    # ARQ worker 就绪前同步跑完返回 ready；接入队列后返回 pending——两者皆合法
    assert r.json()["status"] in {"pending", "ready"}


def test_retrieve_endpoint(app_client):
    text = "生鲜类商品不支持七天无理由退货，坏果按比例赔偿。"
    kb, r = _upload(app_client, "ops.txt", text.encode())
    hits = app_client.post("/api/v1/retrieve", json={
        "query": "生鲜 退货", "kb_ids": [kb["id"]], "top_k": 3}).json()
    assert hits and hits[0]["doc_name"] == "ops.txt"
    assert {"id", "doc_name", "content", "score", "bm25_hit", "vec_hit"} <= set(hits[0])


def test_chat_returns_answer_citations_and_persists_history(app_client, chat_calls):
    text = "客付申请退或换货，先核订单号，要在24小时内响应。"
    kb, r = _upload(app_client, "ops01.txt", text.encode())
    q = "退货要多久响应？"
    resp = app_client.post("/api/v1/chat", json={"question": q, "kb_ids": [kb["id"]]}).json()
    assert resp["answer"].startswith("据资料")
    assert resp["citations"][0]["n"] == 1
    assert resp["citations"][0]["doc_name"] == "ops01.txt"
    assert resp["usage"]["prompt_tokens"] == 100
    conv_id = resp["conversation_id"]

    # 会话历史：user 消息为结构化 content parts，assistant 带 citations
    msgs = app_client.get(f"/api/v1/conversations/{conv_id}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"][0] == {"type": "text", "text": q}
    assert msgs[1]["citations"][0]["chunk_id"] > 0
    assert app_client.get("/api/v1/conversations").json()[0]["id"] == conv_id


def test_chat_miss_fallback_without_llm_call(app_client, chat_calls):
    kb, r = _upload(app_client)
    resp = app_client.post("/api/v1/chat", json={
        "question": "公司食堂几点开门完全无关的问题xx", "kb_ids": [kb["id"]]}).json()
    assert "资料里没有" in resp["answer"]
    assert resp["citations"] == []
    assert chat_calls == [], "未命中兜底不得消耗大模型调用"


def test_404_for_unknown_resources(app_client):
    assert app_client.get("/api/v1/documents/999").status_code == 404
    assert app_client.get("/api/v1/conversations/999/messages").status_code == 404
    kb = app_client.post("/api/v1/kb", json={"name": "x"}).json()
    assert app_client.post(f"/api/v1/kb/{kb['id']}/documents",
                           files={"file": ("a.txt", b"x", "text/plain")},
                           ).status_code == 201


def test_chat_writes_usage_record(app_client, db: Session):
    from app.models import UsageRecord

    text = "生鲜退货政策内容。"
    kb, r = _upload(app_client, "u.txt", text.encode())
    app_client.post("/api/v1/chat", json={"question": "生鲜 退货 政策", "kb_ids": [kb["id"]]})
    rec = db.query(UsageRecord).one()
    assert (rec.scenario, rec.prompt_tokens, rec.completion_tokens) == ("chat", 100, 20)
