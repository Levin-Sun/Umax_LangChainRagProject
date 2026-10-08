# 模型后台（§C：改表即生效；key 加密存储、打码、不回传明文）。
#
# 主密钥（GATEWAY_SECRET）没配就 503：不是"功能降级"，而是**不能假装能存 key**——
# 明文落库比报错危险得多。
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import Runtime, client_ip, get_session, require_admin_role, require_license
from app.api.schemas import ERR, ERR_GATE, SCENARIOS, ModelIn, ModelOut, ModelPatchIn, PathId
from app.api.serializers import model_json
from app.models import ModelConfig, User
from app.services.audit import record as audit_record
from app.services.crypto import encrypt_secret


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/models", status_code=201, response_model=ModelOut,
                 responses={**ERR(400, "scenario 非法"), **ERR(503, "未配置 GATEWAY_SECRET"),
                            **ERR_GATE})
    def create_model(body: ModelIn, request: Request,
                     admin: User = Depends(require_license),
                     session: Session = Depends(get_session)):
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, f"scenario 仅支持 {'/'.join(sorted(SCENARIOS))}")
        m = ModelConfig(tenant_id="default", scenario=body.scenario, provider=body.provider,
                        base_url=body.base_url, model_name=body.model_name,
                        encrypted_api_key=encrypt_secret(body.api_key, rt.require_secret()),
                        capabilities=body.capabilities, is_default=body.is_default,
                        fallback_rank=body.fallback_rank, enabled=body.enabled)
        session.add(m)
        session.flush()
        # detail 只进非敏感定位字段：api_key/base_url/provider 明文一律不入审计
        audit_record(session, "model_created", user_email=admin.email,
                     target_type="model", target_id=m.id,
                     detail={"scenario": body.scenario, "model_name": body.model_name,
                             "fallback_rank": body.fallback_rank}, ip=client_ip(request))
        session.commit()
        return model_json(m, rt.require_secret())

    @router.get("/api/v1/models", response_model=list[ModelOut], responses={**ERR_GATE})
    def list_models(admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        rows = session.query(ModelConfig).order_by(ModelConfig.scenario,
                                                  ModelConfig.fallback_rank, ModelConfig.id)
        return [model_json(m, rt.require_secret()) for m in rows]

    @router.patch("/api/v1/models/{model_id}", response_model=ModelOut,
                  responses={**ERR(400, "scenario 非法"), **ERR(404, "模型配置不存在"),
                             **ERR(503, "未配置 GATEWAY_SECRET"), **ERR_GATE})
    def patch_model(model_id: PathId, body: ModelPatchIn, request: Request,
                    admin: User = Depends(require_license),
                    session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if not m:
            raise HTTPException(404, "模型配置不存在")
        data = body.model_dump(exclude_none=True)
        # detail 只进字段名（调用方请求改哪些字段），值一律不落——尤其 api_key 明文
        fields = sorted(data.keys())
        if "scenario" in data and data["scenario"] not in SCENARIOS:
            raise HTTPException(400, f"scenario 仅支持 {'/'.join(sorted(SCENARIOS))}")
        if "api_key" in data:
            data["encrypted_api_key"] = encrypt_secret(data.pop("api_key"), rt.require_secret())
        for k, v in data.items():
            setattr(m, k, v)
        if fields:   # 无变更不记审计（评审收编⑧/⑩ 统一裁定）：空 PATCH 是"什么都没改"，
            # 记一条 fields=[] 的事件只会污染事件流——与 patch_user 的 `if changed:` 同一口径
            audit_record(session, "model_updated", user_email=admin.email,
                         target_type="model", target_id=m.id, detail={"fields": fields},
                         ip=client_ip(request))
        session.commit()
        return model_json(m, rt.require_secret())

    @router.delete("/api/v1/models/{model_id}", status_code=204,
                   responses={**ERR(503, "未配置 GATEWAY_SECRET"), **ERR_GATE})
    def delete_model(model_id: PathId, request: Request,
                     admin: User = Depends(require_license),
                     session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if m:
            model_name = m.model_name      # 删前取：行没了就查不到被删的是哪个模型
            session.delete(m)
            audit_record(session, "model_deleted", user_email=admin.email,
                         target_type="model", target_id=model_id,
                         detail={"model_name": model_name}, ip=client_ip(request))
            session.commit()

    return router
