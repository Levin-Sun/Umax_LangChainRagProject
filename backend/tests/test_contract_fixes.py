# 契约 fuzz 修复①-⑧的行为锁定回归测试（评审补齐）：
# fuzz 是随机种子驱动且 .hypothesis/ 反例库不入库——未来重构删掉 _clean_text 或 int32 界，
# 随机 fuzz 大概率不会再挖出来、全量仍绿。本文件用确定性 TestClient 断言逐一封死各修复的行为面，
# 期望值全部按 main.py 实际语义推导（int32 边界/StrictBool/规范化/400 声明/Allow 合并）。
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import INT32_MAX, INT32_MIN, create_app
from app.models import Document, KnowledgeBase, ModelConfig
from tests.conftest import login, seed_user
from tests.test_models_api import FakeEmbedder

SECRET = "contract-fixes-secret"
ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
INT32_OVERFLOW = 2**31           # 越上界一格（PG INTEGER 存不下，修复①的动因）
INT32_UNDERFLOW = -(2**31) - 1   # 越下界一格

MODEL_BODY = {"scenario": "chat", "provider": "deepseek",
              "base_url": "https://api.deepseek.com/v1",
              "api_key": "sk-plain-ABCDEFG1234", "model_name": "deepseek-chat",
              "fallback_rank": 0}


@pytest.fixture
def client(engine, db, tmp_path):
    """全员登录后：本文件的 422/400 断言必须登录态发出——FastAPI 先解依赖后校 body，
    匿名请求会先撞 401，"格式违法被拒"与"未登录"两码就混成一团（阶段 2 换轨）。"""
    app = create_app(engine=engine, secret=SECRET,
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     upload_dir=str(tmp_path))
    with TestClient(app) as c:
        seed_user(engine, *ADMIN, role="admin")
        login(c, *ADMIN)
        yield c


def _post_raw(client, url, payload):
    # httpx 的 json= 用 ensure_ascii=False，孤立代理码点在客户端就编不出字节流；
    # 手工按 fuzz 真实形态（\u0000/\udXXX 转义序列的 ASCII 字节体）发送，服务端解码后才还原
    return client.post(url, content=json.dumps(payload).encode("ascii"),
                       headers={"content-type": "application/json"})


# ---- 修复①/⑦：id 的 int32 边界进契约（body + path 两侧，越界 422、界内合法不误伤）----
def test_path_id_out_of_int32_rejected(client):
    assert client.get(f"/api/v1/documents/{INT32_OVERFLOW}").status_code == 422
    assert client.get(f"/api/v1/conversations/{INT32_UNDERFLOW}/messages").status_code == 422
    assert client.get("/api/v1/documents/99999999999999999999999").status_code == 422
    # 边界值本身合法：进业务层查库回 404（若 422 即"拒绝合法请求"违约）
    assert client.get(f"/api/v1/documents/{INT32_MAX}").status_code == 404
    assert client.get(f"/api/v1/documents/{INT32_MIN}").status_code == 404


def test_body_id_out_of_int32_rejected(client):
    r = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": [INT32_OVERFLOW]})
    assert r.status_code == 422
    r = client.post("/api/v1/chat", json={"question": "q", "conversation_id": INT32_OVERFLOW})
    assert r.status_code == 422
    r = client.post("/api/v1/models", json={**MODEL_BODY, "fallback_rank": INT32_OVERFLOW})
    assert r.status_code == 422
    # 界内合法：同字段 INT32_MAX 必须被接受（锁定"卡界不误伤"）
    assert client.post("/api/v1/retrieve",
                       json={"query": "q", "kb_ids": [INT32_MAX]}).status_code == 200
    assert client.post("/api/v1/models",
                       json={**MODEL_BODY, "fallback_rank": INT32_MAX}).status_code == 201


# ---- 修复⑥：body 整数字段按 JSON integer 语义（整值浮点收，bool/str/小数拒）----
def test_json_int_semantics(client):
    assert client.post("/api/v1/retrieve",
                       json={"query": "q", "kb_ids": [3.0]}).status_code == 200
    for bad in (True, "5", 2.5, None, {"x": 1}):
        r = client.post("/api/v1/retrieve", json={"query": "q", "kb_ids": [bad]})
        assert r.status_code == 422, f"kb_ids 含 {bad!r} 应被拒"


# ---- 修复②：布尔字段 StrictBool，禁止 1/0/"true" 等 lax 强转混入 ----
def test_strict_bool_rejects_int_and_str(client):
    r = client.post("/api/v1/models", json={**MODEL_BODY, "enabled": 1})
    assert r.status_code == 422
    r = client.post("/api/v1/models", json={**MODEL_BODY, "is_default": "true"})
    assert r.status_code == 422
    r = client.post("/api/v1/models", json={**MODEL_BODY, "enabled": True})
    assert r.status_code == 201 and r.json()["enabled"] is True


# ---- 修复⑧：NUL/孤立代理码点规范化入库（name + description，PG text 存不下即 500）----
def test_kb_name_nul_and_surrogate_normalized(client, db):
    r = _post_raw(client, "/api/v1/kb", {"name": "a\x00b\ud800c",
                                         "description": "d\x00e\udfff f"})
    assert r.status_code == 201
    assert r.json()["name"] == "ab\ufffdc"          # NUL 剔除、孤立代理→U+FFFD
    assert r.json()["description"] == "de\ufffd f"
    row = db.query(KnowledgeBase).one()              # 真入库成功（未炸 PG、未静默丢字段）
    assert (row.name, row.description) == ("ab\ufffdc", "de\ufffd f")


# ---- 修复⑧：JSONB（capabilities）嵌套串/键同样递归过闸 ----
def test_model_capabilities_nested_normalized(client, db):
    caps = {"a\x00b\ud800": ["x\x00y", {"deep": "\udfffb"}]}
    r = _post_raw(client, "/api/v1/models",
                  {**MODEL_BODY, "capabilities": caps, "model_name": "m\ud800k"})
    assert r.status_code == 201
    assert r.json()["capabilities"] == {"ab\ufffd": ["xy", {"deep": "\ufffdb"}]}  # 键与值都规范
    assert r.json()["model_name"] == "m\ufffdk"
    row = db.query(ModelConfig).one()
    assert row.capabilities == {"ab\ufffd": ["xy", {"deep": "\ufffdb"}]}


# ---- 修复④：请求体解析失败回已声明的 400（_ERR_BODY），非法 JSON 文本仍是 422 面 ----
def test_malformed_body_returns_declared_400(client):
    for url in ("/api/v1/kb", "/api/v1/retrieve", "/api/v1/chat"):
        # content-type 为 json 但字节流连 UTF-8 都解不开 → Starlette/FastAPI 解析层 400
        r = client.post(url, content=b"123\xff\xfe",
                        headers={"content-type": "application/json"})
        assert r.status_code == 400, url
    # 可解码但语法非法 → FastAPI json_invalid → 422（spec 默认 422 面，非 400 面）
    r = client.post("/api/v1/kb", content=b"{oops",
                    headers={"content-type": "application/json"})
    assert r.status_code == 422


# ---- 修复⑤：405/OPTIONS 的 Allow 头 = 同路径多路由方法全集（RFC 9110）----
def test_allow_header_is_full_method_union(client):
    # 注：本版本 FastAPI 的路由 methods 不含 HEAD/OPTIONS（HEAD 请求实测 405），
    # 所以"方法全集"= 该路径各路由声明方法的并集，不含框架未声明的方法
    # /kb 由 POST(create)+GET(list) 两条单方法路由拼成——旧 bug 只报首条路由（Allow 缺 GET）
    r = client.put("/api/v1/kb")
    assert r.status_code == 405
    assert set(r.headers["allow"].split(", ")) == {"GET", "POST"}
    r = client.options("/api/v1/kb")
    assert set(r.headers["allow"].split(", ")) == {"GET", "POST"}
    # /models/{id} 由 PATCH+DELETE 两条路由拼成——旧 bug 只报首条路由（Allow 缺 DELETE）
    r = client.put("/api/v1/models/1")
    assert r.status_code == 405
    assert set(r.headers["allow"].split(", ")) == {"DELETE", "PATCH"}
    # /documents/{id} 由 GET+PATCH 拼成
    assert set(client.options("/api/v1/documents/1").headers["allow"].split(", ")) \
        == {"GET", "PATCH"}


# ---- 修复⑧收口：multipart filename 是唯一不经 pydantic 验证链的入口字符串 ----
def test_upload_filename_nul_normalized(engine, db, tmp_path):
    # filename 直接进 Path(upload_dir)/f"{hex}_{name}" 再 write_bytes：NUL 混入即
    # ValueError/PG DataError → 未声明 500（契约侧 201/404/415 全对不上）。
    # httpx 的 files= 会对 filename 做头编码清洗，掩盖真实形态——手工拼 multipart 原始字节，
    # 确保 \x00 按 fuzz 同形态抵达 handler（python-multipart 不剥 NUL，实测 file.filename 含 \x00）
    app = create_app(engine=engine, secret=SECRET,
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     upload_dir=str(tmp_path))
    # raise_server_exceptions=False：修复前的 ValueError 以 500 形态被断言捕获而非抛进测试
    with TestClient(app, raise_server_exceptions=False) as c:
        seed_user(engine, *ADMIN, role="admin")
        login(c, *ADMIN)
        kb = c.post("/api/v1/kb", json={"name": "k"}).json()["id"]
        boundary = "----contractfix8"
        body = (f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="a\x00b.md"\r\n'
                "Content-Type: text/markdown\r\n"
                f"\r\nhello contract\r\n--{boundary}--\r\n").encode("utf-8")
        r = c.post(f"/api/v1/kb/{kb}/documents", content=body,
                   headers={"content-type": f"multipart/form-data; boundary={boundary}"})
        assert r.status_code == 201, (r.status_code, r.text[:200])
        assert r.json()["name"] == "ab.md"          # NUL 剔除后入库并回显
        row = db.query(Document).one()
        assert row.name == "ab.md" and "\x00" not in row.name
        assert row.storage_path and "\x00" not in row.storage_path


# ---- 修复⑨：varchar 列宽入约——超长字符串是 spec 合法正数据，此前直达 PG 变 500
# StringDataRightTruncation（fuzz 随机挖中 provider 33 字符）。schema 侧声明列宽
# maxLength（生成器不再产超长正例），运行时 422 拒收（默认面已声明），边界值放行。
def test_varchar_widths_declared_and_enforced(client):
    assert client.post("/api/v1/models",
                       json={**MODEL_BODY, "provider": "p" * 33}).status_code == 422
    assert client.post("/api/v1/models",
                       json={**MODEL_BODY, "model_name": "m" * 129}).status_code == 422
    assert client.post("/api/v1/kb", json={"name": "n" * 129}).status_code == 422
    assert client.patch("/api/v1/documents/1", json={"status": "s" * 17}).status_code == 422
    # 边界值必须放行（maxLength=N 收 N 字符）
    assert client.post("/api/v1/models",
                       json={**MODEL_BODY, "provider": "p" * 32}).status_code == 201
