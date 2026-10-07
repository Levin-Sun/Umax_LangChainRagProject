# 模型输出必须过"入库规范化闸"（与请求侧同一道）。
#
# 评审实测：模型答案里混进一个 NUL，`/chat` 直接返回 **未声明的 500**（PG 文本列存不了 NUL）。
# 请求侧的每个字符串都过了 `_clean_text`（fuzz 修复⑧：NUL 剔除、孤立代理→U+FFFD），
# 但**模型返回的文本**此前直接入库——上游抽风/截断就会把一次问答变成 500。
# 两道闸都要有：①客户端层（覆盖 worker/ingest 这些没有端点收口的路径）
# ②存储点（不管 chat_fn 是谁注入的，落库前一定过）。clean_text 幂等，重复无害。
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import Chunk, Document, KnowledgeBase, Message
from app.services.chat import ChatClient
from app.services.text import clean_text
from tests.conftest import login, seed_user
from tests.test_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


def test_clean_text_is_the_single_gate_for_external_text():
    assert clean_text("a\x00b") == "ab"                      # NUL 剔除
    assert clean_text("x\ud800y") == "x\ufffdy"              # 孤立代理 → U+FFFD
    assert clean_text({"k\x00": ["v\x00", {"深\udfff": 1}]}) == {"k": ["v", {"深\ufffd": 1}]}
    assert clean_text(None) is None                          # 非字符串原样返回


def test_chat_client_normalizes_model_output_at_the_boundary():
    """客户端层收口：所有走 ChatClient 的路径（chat/vision/worker 内嵌图注）一并覆盖。"""
    def handler(request):
        # 按真上游的形态发字节：控制字符与孤立代理以 \uXXXX 转义出现在 JSON 里，
        # 解码后才是 NUL / 孤立代理（用 json= 直接构造会先在客户端编码失败）
        payload = {"choices": [{"message": {"content": "答案含\x00控制字符\ud800"}}],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        body = json.dumps(payload, ensure_ascii=True).encode("ascii")
        return httpx.Response(200, content=body,
                              headers={"content-type": "application/json"})
    client = ChatClient(api_key="k", base_url="http://x/v1", model="m",
                        transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out = client.complete([{"role": "user", "content": "q"}])
    assert out["text"] == "答案含控制字符\ufffd"
    assert "\x00" not in out["text"] and "\ud800" not in out["text"]


def _client(engine, tmp_path, chat_fn):
    c = TestClient(create_app(engine=engine, secret="k", embedder=FakeEmbedder(1024),
                              chat_fn=chat_fn, upload_dir=str(tmp_path),
                              spawn=lambda fn: fn()))
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    return c


def test_chat_with_control_chars_in_answer_stays_200_and_stores_clean(engine, db, tmp_path):
    """存储点也必须过闸：chat_fn 是注入的（测试/自定义实现都可能），不能假设它自己干净。"""
    with Session(engine) as s:
        kb = KnowledgeBase(tenant_id="default", name="库")
        s.add(kb); s.flush()
        doc = Document(tenant_id="default", kb_id=kb.id, name="a.txt", status="ready")
        s.add(doc); s.flush()
        s.add(Chunk(tenant_id="default", document_id=doc.id, kb_id=kb.id, chunk_index=0,
                    content="生鲜不支持七天无理由退货", embedding=None))
        s.commit()
        kb_id = kb.id
    c = _client(engine, tmp_path,
                lambda q, hits: {"answer": "不支持七天无理由\x00[1]\ud800", "prompt_tokens": 1,
                                 "completion_tokens": 1})
    r = c.post("/api/v1/chat", json={"question": "生鲜能退吗", "kb_ids": [kb_id]})
    assert r.status_code == 200, f"模型输出带控制字符不该 500：{r.status_code} {r.text[:200]}"
    assert r.json()["answer"] == "不支持七天无理由[1]\ufffd"
    with Session(engine) as s:      # 落库的也是干净的（否则下次读出来一样会炸）
        stored = s.query(Message).filter_by(role="assistant").one()
    assert "\x00" not in stored.content[0]["text"]
