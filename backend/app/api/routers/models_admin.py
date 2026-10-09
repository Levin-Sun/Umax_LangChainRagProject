# 模型后台（§C：改表即生效；key 加密存储、打码、不回传明文）。
#
# 主密钥（GATEWAY_SECRET）没配就 503：不是"功能降级"，而是**不能假装能存 key**——
# 明文落库比报错危险得多。
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import Runtime, client_ip, get_session, require_admin_role, require_license
from app.api.schemas import (ERR, ERR_GATE, SCENARIOS, BundleIn, BundleOut, ModelCatalogOut,
                             ModelIn, ModelOut, ModelPatchIn, PathId)
from app.api.serializers import model_json
from app.models import ModelConfig, User
from app.services.audit import record as audit_record
from app.services.catalog import load_catalog
from app.services.crypto import encrypt_secret


def _probe_png() -> str:
    """试调用的小图（16×16 白底）：1×1 会被部分厂商判"图片过小"报参数错
    （实测 qwen-vl-max 回 InvalidParameter: The image length and width…），
    那会把"模型不可用"误报成失败，试调结论就不可信了。"""
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (255, 255, 255)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _probe(rt: Runtime, scenario: str) -> tuple[bool | None, str | None]:
    """登记后试调一次最小真实请求 → (是否通过, 说明)。

    ok=None 表示"这条能力在当前进程没接线"（不算失败，例如测试装配里 chat_fn=None）。
    为什么要真发一次：标本一定会过期（厂商改名/下线模型），错误必须**当场**回给界面，
    而不是等客户上传完文档才发现走错了端点（真机踩过：模型名写错 → 入库全部 failed）。
    """
    try:
        if scenario == "chat":
            if rt.chat_fn is None:
                return None, "当前进程没有接线对话模型"
            out = rt.chat_fn("连通测试：请只回 OK", [])
            return (bool(out), None if out else "模型没有返回内容")
        if scenario == "embedding":
            if rt.embedder is None:
                return None, "当前进程没有接线向量模型"
            vec = rt.embedder.embed(["连通测试"])[0]
            return (vec is not None, None if vec is not None else "没有返回向量（该厂商可能不支持向量）")
        if scenario == "vision":
            if rt.vision_fn is None:
                return None, "当前进程没有接线视觉模型"
            out = rt.vision_fn(_probe_png())
            return (True, out.get("model") if isinstance(out, dict) else None)
        if scenario == "rerank":
            if rt.rerank is None:
                return None, "当前进程没有接线精排模型"
            hits = rt.rerank("连通测试", [{"id": 0, "doc_name": "probe", "content": "连通测试"}])
            scored = [h for h in (hits or []) if h.get("rerank_score") is not None]
            # 精排走的是"失败降级为原顺序"的包装器：它不会抛错，只会让 rerank_score 全为 None，
            # 所以判据看分数有没有回来，而不是有没有异常。
            return (bool(scored), None if scored else "精排没有返回分数（模型名或端点可能不对）")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return None, None


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

    @router.get("/api/v1/model-catalog", response_model=ModelCatalogOut, responses={**ERR_GATE})
    def model_catalog(admin: User = Depends(require_admin_role)):
        """内置厂商标本：界面据此做「选厂商 → 一键配齐」，客户不必知道四类模型各自的端点与模型名。

        标本是**数据**（app/data/model_catalog.json），厂商上新/改名只改这份文件；
        `verified=false` 的厂商界面会如实标注"未实测"，提醒以厂商文档为准。
        """
        return load_catalog()

    # 路径刻意不走 /models/{...} 之下：那条已被 PATCH/DELETE /models/{model_id} 占用，
    # "bundle" 会被当成 model_id 吃掉（对它的 PATCH 返回 401 而不是 405，契约 fuzz 抓到的）。
    @router.post("/api/v1/model-bundles", status_code=201, response_model=BundleOut,
                 responses={**ERR(400, "厂商不合法，或该厂商没有你勾选的能力"),
                            **ERR(503, "未配置 GATEWAY_SECRET"), **ERR_GATE})
    def create_bundle(body: BundleIn, request: Request,
                      admin: User = Depends(require_license),
                      session: Session = Depends(get_session)):
        """一键配齐：按内置标本把这家厂商的能力一次登记成多条配置，客户只提供一把 key。

        三个刻意的取舍：
        1. **幂等**：同（场景+厂商+地址+模型名）已存在就更新 key 并重新启用，不再插一条——
           客户反复点不该堆出十份重复配置，"换 key 重新配"也因此只有一条路径。
        2. **登记即试调**：逐能力发一次最小真实请求，把厂商的原始报错回给界面。标本会过期，
           错误必须当场可见，而不是等入库全线 failed 才回头查（真机踩过）。
        3. **维度闸**：embedding 维度必须等于库里的 VECTOR(n)，不等直接 400——否则会在
           写库那一步炸（真机踩过 text-embedding-v2 = 1536）。
        """
        secret = rt.require_secret()
        cat = load_catalog()
        vendor = next((v for v in cat["vendors"] if v["id"] == body.vendor_id), None)
        if vendor is None:
            raise HTTPException(400, f"未收录的厂商：{body.vendor_id}")
        want = body.capabilities or [c["key"] for c in vendor["capabilities"]]
        picked = [c for c in vendor["capabilities"] if c["key"] in set(want)]
        if not picked:
            raise HTTPException(400, "这家厂商没有你勾选的能力；自建或长尾厂商请用「手动登记」")
        profiles = {p["id"]: p for p in vendor["profiles"]}
        items: list[dict] = []
        for c in picked:
            base_url = (profiles.get(c["profile"]) or {}).get("base_url", "")
            if c["key"] == "embedding" and c.get("dim") != rt.settings.embedding_dim:
                raise HTTPException(400, f"{c['model']} 的向量维度是 {c.get('dim')}，"
                                          f"与知识库的 VECTOR({rt.settings.embedding_dim}) 不匹配")
            caps = {"tags": [cat["capability_labels"].get(c["key"], c["key"])]}
            if c.get("dim"):
                caps["dim"] = c["dim"]
            row = (session.query(ModelConfig)
                   .filter_by(scenario=c["scenario"], provider=vendor["name"],
                              base_url=base_url, model_name=c["model"]).first())
            action = "updated" if row else "created"
            if row is None:
                row = ModelConfig(tenant_id="default", scenario=c["scenario"], provider=vendor["name"],
                                  base_url=base_url, model_name=c["model"], capabilities=caps,
                                  is_default=False, fallback_rank=0, enabled=True)
                session.add(row)
            row.encrypted_api_key = encrypt_secret(body.api_key, secret)
            row.capabilities = caps
            row.enabled = True
            items.append({"capability": c["key"], "scenario": c["scenario"], "model": c["model"],
                          "base_url": base_url, "action": action, "ok": None, "detail": None})
        audit_record(session, "model_created", user_email=admin.email, target_type="model",
                     target_id=None, detail={"vendor": vendor["id"],
                                             "models": [i["model"] for i in items]},
                     ip=client_ip(request))
        session.commit()
        if body.test:
            for it in items:   # 试调依赖刚落库的行：网关每次调用现读表，所以这里读到的是新配置
                it["ok"], it["detail"] = _probe(rt, it["scenario"])
        return {"vendor": vendor["name"], "items": items}

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
