# 契约 fuzz：schemathesis 按 openapi spec 自动生成请求打 ASGI 应用（不起服务、零外呼），
# 每个响应校验状态码已声明 + body 符合 schema —— "实现偏离 spec 就红"
import pytest
import schemathesis
from fastapi.testclient import TestClient
from schemathesis.config import SchemathesisConfig

from app.core.config import get_settings
from app.main import create_app
from tests.conftest import seed_user
from tests.test_models_api import FakeEmbedder  # 复用假件（tests 目录有 __init__ + pythonpath）

pytestmark = pytest.mark.contract

# 上传端点的"扩展名白名单→415"是写进 spec 的业务规则，但 OpenAPI 无法表达"文件扩展名必须
# ∈ 白名单"，正数据验收启发式会把它当"合法请求被拒"。仅此一处按文档化配置把已声明的 415 列入
# 可接受状态码；其余检查（状态码/模式/服务器错误等）全部保持默认，不放宽。
_cfg = SchemathesisConfig()
_cfg.projects.default.checks.positive_data_acceptance.expected_statuses = [
    "2xx", "401", "403", "404", "409", "415", "429", "5xx"]


class _InjectSession:
    """给所有 fuzz 请求带 admin 会话 cookie：spec 未声明 securityScheme，只能注入头。
    login 端点本身也被 fuzz（带 cookie 调它无碍：成功=重定向新会话，失败在限流键 email|ip 上自隔离）。"""
    def __init__(self, app, cookie: str):
        self.app, self.raw = app, f"umax_session={cookie}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not any(k == b"cookie" for k, _ in scope["headers"]):
            scope = {**scope, "headers": [*scope["headers"], (b"cookie", self.raw)]}
        await self.app(scope, receive, send)


@pytest.fixture
def contract_schema(engine, db, tmp_path):
    """夹具内建 ASGI 应用（登录态：全员登录后匿名只会把 fuzz 退化成 401 压力测试）
    并由 openapi.from_asgi 内省 spec（零外呼、不起服务）。"""
    def fake_chat(query, hits):
        return {"answer": "fuzz[1]", "prompt_tokens": 1, "completion_tokens": 1}
    app = create_app(engine=engine, secret="contract-secret",
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     chat_fn=fake_chat, upload_dir=str(tmp_path))
    seed_user(engine, "contract-admin@x.com", "C-Pass-1234", role="admin")
    probe = TestClient(app)
    probe.post("/api/v1/auth/login",
               json={"email": "contract-admin@x.com", "password": "C-Pass-1234"})
    token = probe.cookies.get("umax_session")
    assert token, "登录夹具自证：拿得到会话 cookie 再谈注入"
    wrapped = _InjectSession(app, token)
    # 注入自证：经包装器打受护端点不得是 401——否则本文件退化成"401 压力测试"（换轨的动因本身）
    assert TestClient(wrapped).get("/api/v1/kb").status_code == 200
    return schemathesis.openapi.from_asgi("/openapi.json", wrapped, config=_cfg)


schema = schemathesis.pytest.from_fixture("contract_schema")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
