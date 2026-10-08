# 交付自检脚本的两条守卫。
#
# 为什么值得测：`scripts/smoke_delivery.py` 平时不跑（只在交付/升级时跑一次），
# 所以它最容易悄悄腐烂——**而且腐烂的时机最糟**：站在客户机器前面才发现脚本调不通。
# 这里用契约（唯一事实源）把它的请求面钉住：端点改名/挪路径时 CI 先红，而不是交付现场先红。
import re
from pathlib import Path

SPEC = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_delivery.py"


def _norm(path: str) -> str:
    """路径参数的**名字**两边不必一致（脚本里叫 doc_id、契约里可能叫 doc_id，也可能不同），
    语义是"那里有个路径参数"，所以比对前统一抹成 {}。"""
    return re.sub(r"\{[^}]*\}", "{}", path)


def test_every_api_path_used_by_smoke_script_exists_in_contract():
    import json

    src = SCRIPT.read_text(encoding="utf-8")
    used = {_norm(m) for m in re.findall(r'"(/api/v1/[^"\s]*)"', src)}
    assert used, "没从自检脚本里解析出任何 /api/v1 路径——脚本结构是不是变了？"
    declared = {_norm(p) for p in json.loads(SPEC.read_text(encoding="utf-8"))["paths"]}
    missing = sorted(used - declared)
    assert not missing, f"自检脚本调了契约里不存在的端点：{missing}"


def test_smoke_script_has_no_hardcoded_credentials():
    """交付脚本里不许出现口令字面量（它会随仓库进客户环境，也会进 CI 日志）。"""
    src = SCRIPT.read_text(encoding="utf-8")
    for bad in ('password="', "password='", 'password = "', 'password = \''):
        # 只允许从 CLI/env/配置取值；出现赋值式字面量即视为硬编码
        assert bad not in src, f"自检脚本出现硬编码口令：{bad}"
