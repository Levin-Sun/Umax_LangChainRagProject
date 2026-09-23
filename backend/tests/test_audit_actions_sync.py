# 收编㉑：AUDIT_ACTIONS 前端镜像 ↔ 后端 ACTIONS 单一事实源同步守护。
# /audit 的 action 查询参数在契约里无 enum（仅 max_length=32），故没有可比对的 spec 枚举；
# 唯一真能同步的两处事实源即 app.services.audit.ACTIONS 与前端 AuditAdmin.tsx 的 AUDIT_ACTIONS 数组。
# 加动作若漏改任一处，本测试即红——避免前端下拉与后端可写动作集静默漂移。
import re
from pathlib import Path

from app.services.audit import ACTIONS

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "AuditAdmin.tsx"


def test_frontend_audit_actions_mirror_matches_backend():
    text = _FRONTEND.read_text(encoding="utf-8")
    m = re.search(r"AUDIT_ACTIONS\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "未在 AuditAdmin.tsx 找到 AUDIT_ACTIONS 数组常量"
    frontend = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert frontend == set(ACTIONS), f"审计动作漂移：仅前端/仅后端 = {frontend ^ set(ACTIONS)}"
