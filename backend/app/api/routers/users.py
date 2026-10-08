# 用户管理 + 库级授权 + 审计查询（spec §3.1/§5，任务 4-5）：admin 独占，写操作全审计。
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import Runtime, client_ip, get_session, require_admin_role, require_license
from app.api.schemas import (ERR, ERR_BODY, ERR_GATE, ROLES, USER_STATUSES, AuditOut, GrantsIn,
                             PathId, QueryInt, UserIn, UserOut, UserPatchIn)
from app.api.serializers import user_json
from app.models import AuditLog, Conversation, KnowledgeBase, User, UserKbGrant, UserSession
from app.services.audit import record as audit_record
from app.services.auth import hash_password


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def grants_map(session: Session) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for g in session.query(UserKbGrant).order_by(UserKbGrant.kb_id):
            out.setdefault(g.user_id, []).append(g.kb_id)
        return out

    def revoke_sessions(session: Session, user_id: int, *, except_hash: str | None = None) -> None:
        q = session.query(UserSession).filter_by(user_id=user_id)
        if except_hash:
            q = q.filter(UserSession.token_hash != except_hash)
        q.delete()

    @router.get("/api/v1/users", response_model=list[UserOut], responses={**ERR_GATE})
    def list_users(admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        g = grants_map(session)
        return [user_json(u, g) for u in session.query(User).order_by(User.id)]

    @router.post("/api/v1/users", status_code=201, response_model=UserOut,
                 responses={**ERR(400, "email 重复或 role 非法"), **ERR_GATE, **ERR_BODY})
    def create_user_api(body: UserIn, request: Request,
                        admin: User = Depends(require_license), session: Session = Depends(get_session)):
        if body.role not in ROLES:
            raise HTTPException(400, "role 仅支持 admin/member")
        if session.query(User).filter_by(tenant_id="default", email=body.email).first():
            raise HTTPException(400, "该邮箱已存在")
        u = User(tenant_id="default", email=body.email, name=body.name or body.email.split("@")[0],
                 hashed_password=hash_password(body.password), role=body.role, status="active",
                 must_change_password=True,   # 口令是 admin 代发的，首登必须本人改
                 daily_token_limit=body.daily_token_limit,
                 monthly_token_limit=body.monthly_token_limit)
        session.add(u)
        session.flush()
        audit_record(session, "user_created", user_email=admin.email, target_type="user",
                     target_id=u.id, detail={"email": u.email, "role": u.role}, ip=client_ip(request))
        session.commit()
        return user_json(u, grants_map(session))

    @router.patch("/api/v1/users/{user_id}", response_model=UserOut,
                  responses={**ERR(400, "不能对当前登录管理员降级/禁用，role/status 非法"),
                             **ERR(404, "用户不存在"), **ERR_GATE, **ERR_BODY})
    def patch_user(user_id: PathId, body: UserPatchIn, request: Request,
                   admin: User = Depends(require_license), session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.id == admin.id and (body.role == "member" or body.status == "disabled"):
            raise HTTPException(400, "不能对当前登录管理员降级或禁用")
        # 终审收口②：守卫是 `is not None` 而非真值判定——schema 里的 ROLE_PATTERN 只是
        # json_schema_extra（生成契约用，不参与校验），空串必须在这里吃 400：否则它跳过校验
        # 又被下面的 `if v is not None` 写循环当真值落库，账号变成不匹配任何角色判定的 role=""。
        if body.role is not None and body.role not in ROLES:
            raise HTTPException(400, "role 仅支持 admin/member")
        if body.status is not None and body.status not in USER_STATUSES:
            raise HTTPException(400, "status 仅支持 active/disabled")
        changed: list[str] = []
        for f in ("name", "role", "status"):
            v = getattr(body, f)
            if v is not None and v != getattr(u, f):
                setattr(u, f, v)
                changed.append(f)
        # 配额：显式带上的字段才动（含 null=清空为不限）——缺席≠清空
        for f in ("daily_token_limit", "monthly_token_limit"):
            if f in body.model_fields_set and getattr(u, f) != getattr(body, f):
                setattr(u, f, getattr(body, f))
                changed.append(f)
        if body.password:
            u.hashed_password = hash_password(body.password)
            changed.append("password")
            # 重置的口令是 admin 输的：目标用户回到门闸后；admin 重置自己不算（口令仍是本人输的，自锁无意义）
            if u.id != admin.id:
                u.must_change_password = True
        if {"role", "status", "password"} & set(changed):
            # 角色/启停/重置口令变更一律吊销（spec §2）；管理员自重置保留当前会话（评审收编⑦）——
            # 与自助改密同语义：吊销的是"其他设备"，不是把操作者从正在做的管理动作里踢出去。
            # 自降级/自禁用在上游已 400，故 except_hash 只会因 password 生效。
            revoke_sessions(session, u.id,
                            except_hash=request.state.session_hash if u.id == admin.id else None)
        if changed:
            audit_record(session, "user_updated", user_email=admin.email, target_type="user",
                         target_id=u.id, detail={"fields": changed}, ip=client_ip(request))
        session.commit()
        return user_json(u, grants_map(session))

    @router.delete("/api/v1/users/{user_id}", status_code=204,
                   responses={**ERR(400, "不能删除当前登录管理员"), **ERR(404, "用户不存在"),
                              **ERR_GATE})
    def delete_user(user_id: PathId, request: Request,
                    admin: User = Depends(require_license),
                    session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.id == admin.id:
            raise HTTPException(400, "不能删除当前登录管理员")
        # 连带清理：本人会话/消息（保密与干净卸载）；sessions/grants 走 FK CASCADE
        session.query(Conversation).filter_by(user_email=u.email).delete()
        email = u.email
        session.delete(u)
        audit_record(session, "user_deleted", user_email=admin.email, target_type="user",
                     target_id=user_id, detail={"email": email}, ip=client_ip(request))
        session.commit()

    @router.get("/api/v1/users/{user_id}/grants", response_model=list[int],
                responses={**ERR(400, "管理员隐式全库，无授权表"), **ERR(404, "用户不存在"),
                           **ERR_GATE})
    def get_grants(user_id: PathId, admin: User = Depends(require_admin_role),
                   session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.role == "admin":
            raise HTTPException(400, "管理员隐式全库，无授权表")
        return sorted(grants_map(session).get(u.id, []))

    @router.put("/api/v1/users/{user_id}/grants", response_model=list[int],
                responses={**ERR(400, "管理员无授权表 / 知识库不存在"), **ERR(404, "用户不存在"),
                           **ERR_GATE, **ERR_BODY})
    def put_grants(user_id: PathId, body: GrantsIn, request: Request,
                   admin: User = Depends(require_license), session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.role == "admin":
            raise HTTPException(400, "管理员隐式全库，无授权表")
        ids = sorted(set(body.kb_ids))
        if ids and len(session.query(KnowledgeBase.id).filter(KnowledgeBase.id.in_(ids)).all()) != len(ids):
            raise HTTPException(400, "存在不存在的知识库 id")
        session.query(UserKbGrant).filter_by(user_id=u.id).delete()
        for k in ids:
            session.add(UserKbGrant(user_id=u.id, kb_id=k))
        audit_record(session, "grants_updated", user_email=admin.email, target_type="user",
                     target_id=u.id, detail={"kb_ids": ids}, ip=client_ip(request))
        session.commit()
        return ids

    # ---- 审计查询（spec §5，任务 5）：admin 独占，过滤+分页，倒序 ----
    @router.get("/api/v1/audit", response_model=list[AuditOut], responses={**ERR_GATE})
    def list_audit(user: Annotated[str | None, Query(max_length=255)] = None,
                   action: Annotated[str | None, Query(max_length=32)] = None,
                   limit: QueryInt = 50, offset: QueryInt = 0,
                   admin: User = Depends(require_admin_role),
                   session: Session = Depends(get_session)):
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        q = session.query(AuditLog)
        if user:
            q = q.filter(AuditLog.user_email == user)
        if action:
            q = q.filter(AuditLog.action == action)
        # (created_at, id) 双键倒序：同事务内 created_at 相同（PG now()=事务起始时刻），
        # 只按时间排会让同批行的分页顺序不确定——id 兜底后全序确定，分页可复现
        rows = (q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset(offset).limit(limit).all())
        return [{"id": r.id, "user_email": r.user_email, "action": r.action,
                 "target_type": r.target_type, "target_id": r.target_id, "detail": r.detail,
                 "ip": r.ip, "created_at": r.created_at.isoformat()} for r in rows]

    return router
