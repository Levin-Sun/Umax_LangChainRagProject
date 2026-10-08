# 白标（§2.2）：品牌名+logo 后台可改。GET 匿名可读（登录页首屏要显品牌，
# 与 /health 同属匿名面）；PUT admin 独占全审计。
#
# app_settings 是通用键值表，配置中心（services/settings）也走它——白标只是第一个租户，
# 所以这里用最朴素的读写，不引入第二套抽象。
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.constants import DEFAULT_BRAND_NAME, LOGO_MAX, LOGO_RE
from app.api.deps import Runtime, client_ip, get_session, require_license
from app.api.schemas import ERR, ERR_BODY, ERR_GATE, BrandingOut, BrandingPut
from app.api.serializers import branding_json
from app.models import AppSetting, User
from app.services.audit import record as audit_record


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def setting_get(session: Session, key: str, default) -> object:
        row = session.get(AppSetting, key)
        return row.value if row else default

    def setting_put(session: Session, key: str, value, by: str) -> None:
        row = session.get(AppSetting, key)
        if row:
            row.value, row.updated_by = value, by
        else:
            session.add(AppSetting(key=key, value=value, updated_by=by))

    def branding_of(session: Session) -> dict:
        return branding_json(setting_get(session, "brand_name", DEFAULT_BRAND_NAME),
                             setting_get(session, "logo", None))

    @router.get("/api/v1/branding", response_model=BrandingOut)
    def get_branding(session: Session = Depends(get_session)):
        return branding_of(session)

    @router.put("/api/v1/branding", response_model=BrandingOut,
                responses={**ERR(400, "logo 仅支持 data:image/* base64（≤400KB）"),
                           **ERR_GATE, **ERR_BODY})
    def put_branding(body: BrandingPut, request: Request,
                     admin: User = Depends(require_license),
                     session: Session = Depends(get_session)):
        # 先全量校验再落库：logo 非法时 brand_name 也不能写进去（部分更新原子性）
        if "logo" in body.model_fields_set and body.logo is not None and (
                not LOGO_RE.match(body.logo) or len(body.logo) > LOGO_MAX):
            raise HTTPException(400, "logo 仅支持 data:image/* base64（≤400KB）")
        fields = []
        if body.brand_name is not None:
            setting_put(session, "brand_name", body.brand_name.strip(), admin.email)
            fields.append("brand_name")
        if "logo" in body.model_fields_set:
            setting_put(session, "logo", body.logo, admin.email)
            fields.append("logo")
        if fields:
            # detail 只记字段名：logo 的 base64 数据体不入审计（肥且无排查价值）
            audit_record(session, "branding_updated", user_email=admin.email,
                         target_type="branding", detail={"fields": fields},
                         ip=client_ip(request))
            session.commit()
        return branding_of(session)

    return router
