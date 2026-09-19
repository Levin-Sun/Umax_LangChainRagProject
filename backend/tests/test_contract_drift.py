# 契约即事实源：改端点必须同步重导 spec，否则此测试红——diff openapi.json 即评审面
import json
from pathlib import Path

from app.main import create_app

SPEC = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def _current_spec() -> str:
    app = create_app(engine=None)  # openapi() 不触库，engine 仅请求期使用
    return json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def test_spec_matches_committed():
    assert SPEC.read_text(encoding="utf-8") == _current_spec()
