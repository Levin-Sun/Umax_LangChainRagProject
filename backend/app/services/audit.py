# 审计统一写入口：只 add 行不 commit——与各写端点同事务，保证"操作成功才有审计"
from app.models import AuditLog

# ACTIONS 是审计动作的单一事实源（SCENARIO_PATTERN 同款收口思路）：前端
# frontend/src/components/AuditAdmin.tsx 的 AUDIT_ACTIONS 下拉镜像必须与此集合逐字一致，
# 由 backend/tests/test_audit_actions_sync.py 守护漂移（加动作漏改任一处即测试红）。
ACTIONS = {
    "login_success", "login_failed", "logout",
    "user_created", "user_updated", "grants_updated",
    "kb_created", "kb_deleted", "document_uploaded", "document_deleted",
    "document_reprocessed", "model_created", "model_updated", "model_deleted",
}


def record(session, action: str, *, user_email: str | None = None,
           target_type: str | None = None, target_id: int | None = None,
           detail: dict | None = None, ip: str | None = None) -> AuditLog:
    assert action in ACTIONS, f"未登记的审计动作：{action}（加动作必须先进 spec §5 事件表）"
    row = AuditLog(tenant_id="default", user_email=user_email, action=action,
                   target_type=target_type, target_id=target_id,
                   detail=detail or {}, ip=ip)
    session.add(row)
    return row
