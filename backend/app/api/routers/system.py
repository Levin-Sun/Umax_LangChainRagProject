# 系统面：/health（匿名，部署与排查用）与 /license（admin，授权状态）。
#
# 两个端点放一起是因为它们回答同一类问题——"这套东西现在是什么状态"：
# health 说进程版本，license 说授权还剩几天。都不属于任何业务域。
from fastapi import APIRouter, Depends
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.api.deps import Runtime, get_session, require_admin_role
from app.api.schemas import ERR_GATE, HealthOut, LicenseOut
from app.core.version import BUILD_COMMIT
from app.models import User


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/health", response_model=HealthOut)
    def health(session: Session = Depends(get_session)):
        session.execute(sa_text("SELECT 1"))
        # commit/started_at：排查"改了不生效"的第一手信息——先确认进程跑的是哪份代码
        return {"status": "ok", "commit": BUILD_COMMIT, "started_at": rt.started_at}

    @router.get("/api/v1/license", response_model=LicenseOut, responses={**ERR_GATE})
    def get_license(admin: User = Depends(require_admin_role)):
        st = rt.license_status()
        return {"enforced": st.enforced, "valid": st.valid, "reason": st.reason,
                "license_key": st.license_key, "customer": st.customer,
                "issued_at": st.issued_at, "expires_at": st.expires_at,
                "days_left": st.days_left, "features": st.features,
                "machine_fingerprint": st.machine_fingerprint}

    return router
