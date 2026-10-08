# 运行时配置中心（§D）：提示词/检索/切块参数后台可改，每请求现读＝改完即生效。
#
# 请求体（SettingsPutIn）是按 SPEC 动态建模的，所以在装配期由 create_app 建好放在 Runtime 上——
# 它是"按当前 SPEC 生成的一份 schema"，不是手写的类。
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import Runtime, client_ip, get_session, require_admin_role, require_license
from app.api.schemas import ERR_BODY, ERR_GATE, SettingsSnapshotOut
from app.models import User
from app.services.audit import record as audit_record


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/settings", response_model=SettingsSnapshotOut, responses={**ERR_GATE})
    def get_settings_api(admin: User = Depends(require_admin_role)):
        return rt.cfg.snapshot()

    @router.put("/api/v1/settings", response_model=SettingsSnapshotOut,
                responses={**ERR_GATE, **ERR_BODY})
    def put_settings_api(body: rt.settings_put_model, request: Request,
                         admin: User = Depends(require_license),
                         session: Session = Depends(get_session)):
        # 只处理客户端真正带上的字段（model_fields_set）：缺席=不动，显式 null=回默认
        changes = {k: getattr(body, k) for k in body.model_fields_set}
        if changes:
            changed = rt.cfg.put(session, changes, admin.email)
            if changed:
                audit_record(session, "settings_updated", user_email=admin.email,
                             target_type="settings", detail={"fields": sorted(changed)},
                             ip=client_ip(request))
                session.commit()
        return rt.cfg.snapshot()

    return router
