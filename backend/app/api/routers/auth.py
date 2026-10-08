# 鉴权面（RBAC spec §2）：登录/登出/我的身份/自助改密。
#
# 登录节流器（LoginThrottle）挂在 Runtime 上而不是模块级单例：**一个 app 实例一份**，
# 否则测试之间会互相污染（前一个用例的失败计数会让后一个用例莫名 429）。
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.constants import SESSION_COOKIE, SESSION_TTL_DAYS
from app.api.deps import Runtime, allowed_kb_ids, client_ip, get_session, get_user, now
from app.api.schemas import (ERR, ERR_BODY, ERR_UNAUTH, AuthMeOut, ChangePasswordIn, LoginIn)
from app.models import User, UserSession
from app.services.audit import record as audit_record
from app.services.auth import hash_password, new_session_token, verify_password


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/auth/login", status_code=204,
                 responses={**ERR(401, "邮箱或口令错误"),
                            **ERR(429, "失败次数过多，15 分钟后再试"), **ERR_BODY})
    def auth_login(body: LoginIn, request: Request, response: Response,
                   session: Session = Depends(get_session)):
        key = f"{body.email}|{client_ip(request)}"
        if rt.throttle.blocked(key):
            # 文案与 spec 里 429 的 description 一字对齐（评审收编③）：同一码两处措辞必然漂移
            raise HTTPException(429, "失败次数过多，15 分钟后再试")
        u = session.query(User).filter_by(tenant_id="default", email=body.email).first()
        if not u or not verify_password(body.password, u.hashed_password) or u.status != "active":
            rt.throttle.failure(key)
            audit_record(session, "login_failed", user_email=body.email, ip=client_ip(request))
            session.commit()   # 审计必须先落，再抛 401
            raise HTTPException(401, "邮箱或口令错误")
        rt.throttle.success(key)
        plain, token_hash = new_session_token()
        session.add(UserSession(token_hash=token_hash, user_id=u.id,
                                expires_at=now() + timedelta(days=SESSION_TTL_DAYS)))
        audit_record(session, "login_success", user_email=u.email, target_type="user",
                     target_id=u.id, ip=client_ip(request))
        session.commit()
        # secure 标记留生产硬化：HTTPS 终结在反代后时加 secure=True 即可（评审收编⑤）；
        # 当前 dev/测试是 http://127.0.0.1，加了 cookie 直接被丢弃，全链路登录态失效
        response.set_cookie(SESSION_COOKIE, plain, httponly=True, samesite="lax",
                            max_age=SESSION_TTL_DAYS * 24 * 3600, path="/")

    @router.post("/api/v1/auth/logout", status_code=204, responses=ERR_UNAUTH)
    def auth_logout(request: Request, response: Response,
                    user: User = Depends(get_user), session: Session = Depends(get_session)):
        row = session.get(UserSession, request.state.session_hash)
        if row:
            session.delete(row)
        audit_record(session, "logout", user_email=user.email, ip=client_ip(request))
        session.commit()
        response.delete_cookie(SESSION_COOKIE, path="/")

    @router.get("/api/v1/auth/me", response_model=AuthMeOut, responses=ERR_UNAUTH)
    def auth_me(user: User = Depends(get_user), session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        return {"email": user.email, "name": user.name, "role": user.role,
                "kb_ids": None if allowed is None else sorted(allowed),
                "must_change_password": user.must_change_password}

    @router.post("/api/v1/auth/change-password", status_code=204,
                 # 422 不覆写：FastAPI 默认 422（HTTPValidationError，detail 是数组）——用 ERR(ErrorOut)
                 # 覆写会被 fuzz 判 response_schema_conformance 违约（JSON 解析失败先于依赖 401 发生）。
                 # 401 合并顺序（评审收编②）：ERR_UNAUTH 在前、业务文案在后——同一状态码只有一个
                 # description，端点专属的"旧口令错误"必须胜过通用"需要登录"（后者由 detail 承载）。
                 responses={**ERR_UNAUTH, **ERR(401, "旧口令错误"), **ERR_BODY})
    def auth_change_password(body: ChangePasswordIn, request: Request,
                             user: User = Depends(get_user), session: Session = Depends(get_session)):
        if not verify_password(body.old_password, user.hashed_password):
            raise HTTPException(401, "旧口令错误")
        user.hashed_password = hash_password(body.new_password)
        user.must_change_password = False   # 本人设定了新口令，门闸解除
        session.query(UserSession).filter(
            UserSession.user_id == user.id,
            UserSession.token_hash != request.state.session_hash).delete()   # 踢其他设备，留当前
        audit_record(session, "user_updated", user_email=user.email, target_type="user",
                     target_id=user.id, detail={"fields": ["self_password"]}, ip=client_ip(request))
        session.commit()

    return router
