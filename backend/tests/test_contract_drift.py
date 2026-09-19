# 契约即事实源：改端点必须同步重导 spec，否则此测试红——diff openapi.json 即评审面
import json
from pathlib import Path

from app.main import SCENARIO_PATTERN, create_app

SPEC = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def _current_spec() -> str:
    app = create_app(engine=None)  # openapi() 不触库，engine 仅请求期使用
    return json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def test_spec_matches_committed():
    assert SPEC.read_text(encoding="utf-8") == _current_spec()


# 终审收口 I3：scenario 约束的单一事实源守护——入库契约里的 pattern 字面量必须等于
# 运行时 SCENARIOS 派生的 SCENARIO_PATTERN（改集合后忘重导 spec / 手改 JSON 都会红）。
def test_scenario_pattern_in_contract_is_single_sourced():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    assert schemas["ModelIn"]["properties"]["scenario"]["pattern"] == SCENARIO_PATTERN
    any_of = schemas["ModelPatchIn"]["properties"]["scenario"]["anyOf"]
    patterned = [s for s in any_of if "pattern" in s]
    assert len(patterned) == 1
    assert patterned[0]["pattern"] == SCENARIO_PATTERN
