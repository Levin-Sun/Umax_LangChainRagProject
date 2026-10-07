# TDD 红灯：chat 服务（OpenAI 兼容 /chat/completions，重试退避，迁自 stage0 generate.py）
import json

import httpx
import pytest

from app.services.chat import RETRY_BACKOFF, ChatClient, build_user_prompt


def _transport(seq):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        item = seq[min(len(calls) - 1, len(seq) - 1)]
        if item == "boom":
            raise httpx.ConnectError("reset")
        return httpx.Response(item[0], json=item[1])

    return httpx.MockTransport(handler), calls


OK = (200, {"choices": [{"message": {"content": "答案[1]"}}],
            "usage": {"prompt_tokens": 630, "completion_tokens": 120}})


def _client(seq, calls_holder=None):
    tp, calls = _transport(seq)
    return ChatClient(api_key="k", base_url="https://x/v1", model="m",
                      transport=tp, sleep=lambda s: None), calls


def test_complete_returns_text_and_usage():
    c, calls = _client([OK])
    out = c.complete([{"role": "user", "content": "hi"}])
    assert out == {"text": "答案[1]", "prompt_tokens": 630, "completion_tokens": 120}
    assert calls[0]["model"] == "m" and calls[0]["temperature"] == 0.2


def test_retry_then_success():
    c, calls = _client(["boom", OK])
    assert c.complete([{"role": "user", "content": "hi"}])["text"] == "答案[1]"
    assert len(calls) == 2


def test_gives_up_after_all_backoffs():
    c, calls = _client([(500, {})])
    with pytest.raises(httpx.HTTPStatusError):
        c.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == len(RETRY_BACKOFF) + 1


def test_user_prompt_numbers_materials_for_citation():
    hits = [{"doc_name": "a.pdf", "content": "甲内容"},
            {"doc_name": "b.pdf", "content": "乙内容"}]
    p = build_user_prompt("能退吗？", hits)
    assert "[1] （来源：a.pdf）" in p and "[2] （来源：b.pdf）" in p
    assert p.rstrip().endswith("【问题】能退吗？")


# ---- 网关在调用期抛错（模型全挂/被删光）不得 500：问答端点转未命中兜底 ----
def test_chat_endpoint_converts_gateway_failure_to_miss(engine, db, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.gateway import GatewayError
    from tests.conftest import login, seed_user

    def broken_chat(query, hits):
        raise GatewayError("全部 chat 模型均调用失败")

    app = create_app(engine=engine, secret="s", embedder=None, chat_fn=broken_chat,
                     upload_dir=str(tmp_path))
    seed_user(engine, "admin@x.com", "Adm1n-Pass-123", role="admin")
    c = TestClient(app)
    login(c, "admin@x.com", "Adm1n-Pass-123")
    kb = c.post("/api/v1/kb", json={"name": "k"}).json()
    raw = "售后规则：生鲜商品不支持七天无理由退货。" * 5
    assert c.post(f"/api/v1/kb/{kb['id']}/documents",
                  files={"file": ("a.txt", raw.encode(), "text/plain")}).status_code == 201
    r = c.post("/api/v1/chat", json={"question": "生鲜能退吗", "kb_ids": [kb["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "资料里没有相关内容，无法回答。"


# ---- 体验反馈④（2026-10-07）：会话删除——仅本人（别人的/不存在同文案 404），消息级联清 ----
def test_delete_conversation_owner_only_cascades(engine, db, tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.main import create_app
    from app.models import Message
    from tests.conftest import login, seed_user

    app = create_app(engine=engine, secret="s", embedder=None,
                     chat_fn=lambda q, h: {"answer": "a", "prompt_tokens": 1, "completion_tokens": 1},
                     upload_dir=str(tmp_path))
    seed_user(engine, "a@x.com", "Passw0rd-1", role="admin")   # 建库需 admin；会话按邮箱隔离不受角色影响
    seed_user(engine, "b@x.com", "Passw0rd-1", role="member")
    c = TestClient(app)
    login(c, "a@x.com", "Passw0rd-1")
    kb = c.post("/api/v1/kb", json={"name": "k"}).json()
    conv = c.post("/api/v1/chat", json={"question": "你好", "kb_ids": [kb["id"]]}).json()["conversation_id"]
    # 别人的会话与不存在的会话同文案 404（不可探测）
    c2 = TestClient(app)
    login(c2, "b@x.com", "Passw0rd-1")
    assert c2.delete(f"/api/v1/conversations/{conv}").status_code == 404
    assert c.delete("/api/v1/conversations/99999").status_code == 404
    # 本人删除 204，消息级联清空，列表不再出现
    assert c.delete(f"/api/v1/conversations/{conv}").status_code == 204
    with Session(engine) as s:
        assert s.query(Message).filter_by(conversation_id=conv).count() == 0
    assert all(x["id"] != conv for x in c.get("/api/v1/conversations").json())
