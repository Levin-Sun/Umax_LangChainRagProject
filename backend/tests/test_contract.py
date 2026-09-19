# 契约 fuzz：schemathesis 按 openapi spec 自动生成请求打 ASGI 应用（不起服务、零外呼），
# 每个响应校验状态码已声明 + body 符合 schema —— "实现偏离 spec 就红"
import pytest
import schemathesis
from schemathesis.config import SchemathesisConfig

from app.core.config import get_settings
from app.main import create_app
from tests.test_models_api import FakeEmbedder  # 复用假件（tests 目录有 __init__ + pythonpath）

pytestmark = pytest.mark.contract

# 上传端点的"扩展名白名单→415"是写进 spec 的业务规则，但 OpenAPI 无法表达"文件扩展名必须
# ∈ 白名单"，正数据验收启发式会把它当"合法请求被拒"。仅此一处按文档化配置把已声明的 415 列入
# 可接受状态码；其余检查（状态码/模式/服务器错误等）全部保持默认，不放宽。
_cfg = SchemathesisConfig()
_cfg.projects.default.checks.positive_data_acceptance.expected_statuses = [
    "2xx", "401", "403", "404", "409", "415", "429", "5xx"]


@pytest.fixture
def contract_schema(engine, db, tmp_path):
    """夹具内建 ASGI 应用并由 openapi.from_asgi 内省 spec（零外呼、不起服务）。"""
    def fake_chat(query, hits):
        return {"answer": "fuzz[1]", "prompt_tokens": 1, "completion_tokens": 1}
    app = create_app(engine=engine, secret="contract-secret",
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     chat_fn=fake_chat, upload_dir=str(tmp_path))
    return schemathesis.openapi.from_asgi("/openapi.json", app, config=_cfg)


schema = schemathesis.pytest.from_fixture("contract_schema")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
