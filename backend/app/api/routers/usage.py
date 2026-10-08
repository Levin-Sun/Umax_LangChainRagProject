# 用量与配额（§C：AI 要花钱，得有闸门）。
#
# 两个视角共用同一份口径（Runtime.quota_state / enforce_quota）：member 看自己（/usage/me），
# admin 看"谁快超了"（/usage/users）；/usage/summary 是成本折算的事实来源。
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import Runtime, get_session, get_user, require_admin_role
from app.api.schemas import ERR_GATE, ERR_LOGIN_GATE, QuotaOut, UsageSummaryOut, UsageUserOut
from app.models import UsageRecord, User


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/usage/me", response_model=QuotaOut, responses={**ERR_LOGIN_GATE})
    def usage_me(user: User = Depends(get_user), session: Session = Depends(get_session)):
        return rt.quota_state(session, user)

    @router.get("/api/v1/usage/users", response_model=list[UsageUserOut], responses={**ERR_GATE})
    def usage_users(admin: User = Depends(require_admin_role),
                    session: Session = Depends(get_session)):
        """按人看用量与限额（"谁快超了"的一眼视图；被拦状态由 exceeded 字段承载，不写审计避免刷屏）。"""
        rows = []
        for u in session.query(User).order_by(User.id):
            rows.append({"id": u.id, "email": u.email, "name": u.name,
                         **rt.quota_state(session, u)})
        return rows

    # ---- 用量看板简版（§C：成本折算的事实来源）----
    @router.get("/api/v1/usage/summary", response_model=list[UsageSummaryOut], responses={**ERR_GATE})
    def usage_summary(admin: User = Depends(require_admin_role),
                      session: Session = Depends(get_session)):
        rows = (session.query(UsageRecord.scenario, UsageRecord.model,
                              func.count().label("calls"),
                              func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
                              func.coalesce(func.sum(UsageRecord.completion_tokens), 0))
                .group_by(UsageRecord.scenario, UsageRecord.model).all())
        return [{"scenario": r[0], "model": r[1], "calls": r[2],
                 "prompt_tokens": int(r[3]), "completion_tokens": int(r[4])} for r in rows]

    return router
