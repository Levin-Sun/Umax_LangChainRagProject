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
    # 兜底话术必须带成因：这里检索是命中的（有文档、BM25 能召回），失败的是模型调用。
    # 真机踩中（2026-10-09）：界面只说"资料里没有相关内容"，人就去重建索引、翻文档，白忙一场。
    assert r.json()["degraded_reason"] == "model_error"


def test_chat_degraded_reason_distinguishes_no_hit_and_no_model(engine, db, tmp_path):
    """三种兜底成因各归其位：检索空=no_hit、没有 chat 通路=no_model、正常作答=null。"""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import login, seed_user

    def _app(chat_fn):
        return create_app(engine=engine, secret="s", embedder=None, chat_fn=chat_fn,
                          upload_dir=str(tmp_path))

    seed_user(engine, "admin@x.com", "Adm1n-Pass-123", role="admin")
    # 没有 chat 通路：端点按未命中兜底，但成因是"没模型"而不是"资料里没有"
    c = TestClient(_app(None))
    login(c, "admin@x.com", "Adm1n-Pass-123")
    kb = c.post("/api/v1/kb", json={"name": "k"}).json()
    r = c.post("/api/v1/chat", json={"question": "空库里问一句", "kb_ids": [kb["id"]]})
    assert r.status_code == 200
    assert r.json()["degraded_reason"] == "no_hit"      # 库里一个字都没有：检索先空

    c2 = TestClient(_app(lambda q, hits: {"answer": "答[1]", "prompt_tokens": 1,
                                          "completion_tokens": 1}))
    login(c2, "admin@x.com", "Adm1n-Pass-123")
    kb2 = c2.post("/api/v1/kb", json={"name": "k2"}).json()
    raw = "售后规则：生鲜商品不支持七天无理由退货。" * 5
    c2.post(f"/api/v1/kb/{kb2['id']}/documents",
            files={"file": ("a.txt", raw.encode(), "text/plain")})
    ok = c2.post("/api/v1/chat", json={"question": "生鲜能退吗", "kb_ids": [kb2["id"]]})
    assert ok.json()["degraded_reason"] is None          # 正常作答：不进降级态


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


# ---- 会话搜索（backlog：覆盖归档诉求的痛点——找得回，而不是归起来）----
def test_list_conversations_search_by_title(engine, db, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import login, seed_user

    app = create_app(engine=engine, secret="s", embedder=None,
                     chat_fn=lambda q, h: {"answer": "a", "prompt_tokens": 1, "completion_tokens": 1},
                     upload_dir=str(tmp_path))
    seed_user(engine, "a@x.com", "Passw0rd-1", role="admin")
    c = TestClient(app)
    login(c, "a@x.com", "Passw0rd-1")
    for q in ("退货政策是什么", "FBA 备货计划", "退货时限几天"):
        c.post("/api/v1/chat", json={"question": q})
    assert len(c.get("/api/v1/conversations").json()) == 3
    hit = c.get("/api/v1/conversations", params={"q": "退货"}).json()
    assert {x["title"] for x in hit} == {"退货政策是什么", "退货时限几天"}
    assert c.get("/api/v1/conversations", params={"q": "不存在词"}).json() == []
    # 通配符不当作语法：字面匹配
    assert len(c.get("/api/v1/conversations", params={"q": "%"}).json()) == 0


# ---- 传图提问放开（阶段 2；数据结构一期已按多模态预留）----
PNG_1PX = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
           "h6FO1AAAAABJRU5ErkJggg==")


def _vision_app(engine, tmp_path, vision_fn=None):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import login, seed_user

    seen = {}

    def chat(query, hits):
        seen["query"] = query
        return {"answer": "答[1]", "prompt_tokens": 1, "completion_tokens": 1}

    app = create_app(engine=engine, secret="s", embedder=None, chat_fn=chat,
                     vision_fn=vision_fn, upload_dir=str(tmp_path))
    seed_user(engine, "a@x.com", "Passw0rd-1", role="admin")
    c = TestClient(app)
    login(c, "a@x.com", "Passw0rd-1")
    return c, seen


def test_chat_with_images_stores_multimodal_parts(engine, db, tmp_path):
    c, seen = _vision_app(engine, tmp_path)
    r = c.post("/api/v1/chat", json={"question": "这张图里是什么", "images": [PNG_1PX]})
    assert r.status_code == 200, r.text
    conv = r.json()["conversation_id"]
    msgs = c.get(f"/api/v1/conversations/{conv}/messages").json()
    parts = msgs[0]["content"]
    assert parts[0]["type"] == "text" and parts[0]["text"] == "这张图里是什么"
    assert parts[0]["image_url"] is None          # 显式声明的可选键：文本片段没有图，如实为 null
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"] == PNG_1PX


def test_chat_image_validation(engine, db, tmp_path):
    c, _ = _vision_app(engine, tmp_path)
    assert c.post("/api/v1/chat", json={"question": "q", "images": ["https://x/a.png"]}).status_code == 400
    assert c.post("/api/v1/chat", json={"question": "q", "images": ["data:text/html;base64,PGI+"]}).status_code == 400
    assert c.post("/api/v1/chat", json={"question": "q",
                                        "images": ["data:image/png;base64," + "A" * 2_000_001]}).status_code == 400
    assert c.post("/api/v1/chat", json={"question": "q", "images": [PNG_1PX] * 4}).status_code == 400
    assert c.post("/api/v1/chat", json={"question": "q", "images": []}).status_code == 200  # 空数组=没图


def test_vision_caption_feeds_retrieval_and_generation_and_usage(engine, db, tmp_path):
    from sqlalchemy.orm import Session

    from app.models import UsageRecord

    def vision_fn(data_url):
        return {"caption": "图片：退款流程图，含 7 个自然日字样", "prompt_tokens": 50,
                "completion_tokens": 30, "model": "fake-vision"}

    c, seen = _vision_app(engine, tmp_path, vision_fn=vision_fn)
    raw = "售后规则：商品自签收后 7 个自然日内可退货。" * 5
    kb = c.post("/api/v1/kb", json={"name": "k"}).json()
    c.post(f"/api/v1/kb/{kb['id']}/documents", files={"file": ("a.txt", raw.encode(), "text/plain")})
    r = c.post("/api/v1/chat", json={"question": "图里的流程要几天", "images": [PNG_1PX]})
    assert r.status_code == 200
    # 图片描述进生成提示词（命中检索+模型都能看到）
    assert "退款流程图" in seen["query"]
    with Session(engine) as s:
        rows = s.query(UsageRecord).filter_by(scenario="vision").all()
        assert len(rows) == 1 and rows[0].model == "fake-vision"
        assert rows[0].completion_tokens == 30


def test_chat_images_without_vision_model_degrades_to_text(engine, db, tmp_path):
    c, seen = _vision_app(engine, tmp_path, vision_fn=None)
    kb = c.post("/api/v1/kb", json={"name": "k"}).json()
    raw = "售后规则：商品自签收后 7 个自然日内可退货。" * 5
    c.post(f"/api/v1/kb/{kb['id']}/documents", files={"file": ("a.txt", raw.encode(), "text/plain")})
    r = c.post("/api/v1/chat", json={"question": "退货要几个自然日", "kb_ids": [kb["id"]], "images": [PNG_1PX]})
    assert r.status_code == 200   # 没配视觉模型：文字照常答，图片仍在消息里可回看
    assert "图片描述" not in seen["query"]
