# FastAPI 服务层：知识库/文档入库/检索/带引用问答/会话历史
# 鉴权收口（阶段 2·任务 6）：除 /health 与 /auth/login 外全部端点要求登录会话；
# admin 面（kb 写/文档写/models/usage/users/grants/audit）另加角色校验，member 得 403。
# 库级授权在检索层钳制（services/retrieval.allowed_kb_ids），不在展示层过滤。
# 旧 ADMIN_TOKEN 共享口令方案（require_admin/admin_session/admin_hint//admin/*）已整体退役，
# 不留兼容层；初始管理员由 build_production_app 按 ADMIN_EMAIL/ADMIN_PASSWORD 播种。
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, BeforeValidator, Field, StrictBool
from sqlalchemy import func, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.models import (AuditLog, Chunk, Conversation, Document, KnowledgeBase, Message,
                        ModelConfig, User, UserKbGrant, UserSession, UsageRecord)
from app.services.audit import record as audit_record
from app.services.auth import (LoginThrottle, hash_password, new_session_token,
                               token_digest, verify_password)
from app.services.citations import parse_citations
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.ingest import ingest_document, read_stored, supported_ext
from app.services.retrieval import retrieve


SCENARIOS = {"chat", "embedding", "rerank", "vision"}
# scenario 约束的单一事实源：SCENARIOS 派生（sorted 保序：chat|embedding|rerank|vision，
# 与已入库 contracts/openapi.json 逐字一致，改集合必须走契约工作流重导 spec）。
# 终审收口 I3：原为手写正则字面量，与运行时校验集合存在漂移风险。
SCENARIO_PATTERN = "^(" + "|".join(sorted(SCENARIOS)) + ")$"
# role/status 枚举同款派生（任务 4）：约束写进 schema，运行时校验集合是同一事实源，防漂移
ROLES = {"admin", "member"}
USER_STATUSES = {"active", "disabled"}
ROLE_PATTERN = "^(" + "|".join(sorted(ROLES)) + ")$"
USER_STATUS_PATTERN = "^(" + "|".join(sorted(USER_STATUSES)) + ")$"
# 契约 fuzz 修复①：id 落 PG INTEGER（int32）——裸 integer 无界，fuzz 发 2^31 直接 SQL 溢出 500，
# 把 int32 边界写进契约。修复②：body 侧 Strict* 禁 bool/str 混入（lax 强转被 fuzz 判"违法请求被接受"）
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


def _json_int(v):
    # 按 JSON Schema 的 integer 语义对齐契约（fuzz 修复⑥）：整值浮点（-74.0）合法必须收，
    # bool/str/None/dict 等非法要拒——pydantic strict 过紧（拒 -74.0），lax 过松（收 True/"5"）
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("不是 JSON integer")
    if isinstance(v, float):
        if not v.is_integer():
            raise ValueError("不是 JSON integer")
        return int(v)
    return v


def _json_int32(v):
    # fuzz 修复⑦：Field(ge/le) 叠在 BeforeValidator 外层时 pydantic 吐非法 JSON Schema 键
    # "ge"/"le"（裸 Field 的 PathId 则正确吐 minimum/maximum）——契约校验器忽略未知键，
    # 越界值被判合法请求，运行时 422 即"拒绝合法请求"违约。改由验证器统一卡 int32 边界，
    # schema 用 json_schema_extra 显式写标准 minimum/maximum，两侧严格一致。
    v = _json_int(v)
    if not (INT32_MIN <= v <= INT32_MAX):
        raise ValueError("超出 int32（PG INTEGER）范围")
    return v


JsonInt = Annotated[int, BeforeValidator(_json_int)]
PathId = Annotated[int, Field(ge=INT32_MIN, le=INT32_MAX)]     # 路径 id：字符串→int 保持 lax，只卡 int32
ReqId = Annotated[int, BeforeValidator(_json_int32),           # body id：JSON integer 且卡 int32，
            Field(json_schema_extra={"minimum": INT32_MIN, "maximum": INT32_MAX})]  # schema 侧标准键


def _query_int(v):
    # 查询参数专用整数口径（任务 5 /audit 分页）：HTTP query 到 FastAPI 手上永远是字符串，
    # 直接套 JsonInt 会把 limit=1 的 "1" 判成"不是 JSON integer"→422（分页端点不可用）；
    # 数字串按值取整，垃圾串/bool/浮点串仍拒（422 由 FastAPI 默认声明入约）。
    # 上下界不在这里卡——端点内钳位（limit 1..200 / offset ≥0），越界不是违法请求。
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            raise ValueError("查询参数不是整数") from None
    return _json_int(v)


QueryInt = Annotated[int, BeforeValidator(_query_int)]        # query 侧整数（limit/offset）


def _clean_text(v):
    # fuzz 修复⑧：契约里这些字段是 type:string，含 NUL(\u0000)/JSON 转义 \udXXX 孤立代理码点的
    # 字符串是合法正数据，但 PG 文本列存不了（NUL 直接 DataError、孤立代理无法编 UTF-8 →
    # UntranslatableCharacter，都是 500 违约）；而拒收 422 又犯"拒绝合法请求"违约，且 JSONB
    # 嵌套串（capabilities 值）在 schema 里根本无法表达排除约束。唯一两侧自洽的行为=规范化入库：
    # NUL 剔除、孤立代理→U+FFFD，dict/list 递归下钻（键与值都处理）。
    if isinstance(v, str):
        if "\x00" in v:
            v = v.replace("\x00", "")
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            v = "".join("\ufffd" if "\ud800" <= c <= "\udfff" else c for c in v)
        return v
    if isinstance(v, dict):
        return {_clean_text(k): _clean_text(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_text(x) for x in v]
    return v


def _utf8_str(v):
    return _clean_text(v)


Utf8Str = Annotated[str, BeforeValidator(_utf8_str)]           # 落库字符串统一过 NUL/代理规范化闸
JsonSafe = Annotated[dict, BeforeValidator(_utf8_str)]         # JSONB 列（capabilities）递归过闸


class KbIn(BaseModel):
    name: Utf8Str = Field(max_length=128)          # 修复⑨：varchar 列宽入约（PG String(128)）
    description: Utf8Str | None = None


class RetrieveIn(BaseModel):
    query: Utf8Str
    kb_ids: list[ReqId] | None = None
    top_k: JsonInt | None = None


class ChatIn(BaseModel):
    question: Utf8Str
    kb_ids: list[ReqId] | None = None
    conversation_id: ReqId | None = None


class DocPatchIn(BaseModel):
    status: Utf8Str | None = Field(None, max_length=16)   # 修复⑨：PG String(16)
    error: Utf8Str | None = None


class ModelIn(BaseModel):
    # scenario 枚举约束写进 schema（pattern）：运行时仍走 400 业务校验，但契约生成器不再
    # 拿任意合法字符串撞出 400 被判"合法请求被拒"（fuzz 修复③：约束没进 spec 才是根因）
    scenario: str = Field(json_schema_extra={"pattern": SCENARIO_PATTERN})
    provider: Utf8Str = Field(max_length=32)              # 修复⑨：PG String(32)
    base_url: Utf8Str                                     # Text 无界
    api_key: Utf8Str                                      # 加密后落 Text
    model_name: Utf8Str = Field(max_length=128)           # 修复⑨：PG String(128)
    capabilities: JsonSafe = {}
    is_default: StrictBool = False
    fallback_rank: ReqId = 0
    enabled: StrictBool = True


class ModelPatchIn(BaseModel):
    scenario: Annotated[str, Field(json_schema_extra={"pattern": SCENARIO_PATTERN})] | None = None
    provider: Utf8Str | None = Field(None, max_length=32)
    base_url: Utf8Str | None = None
    api_key: Utf8Str | None = None
    model_name: Utf8Str | None = Field(None, max_length=128)
    capabilities: JsonSafe | None = None
    is_default: StrictBool | None = None
    fallback_rank: ReqId | None = None
    enabled: StrictBool | None = None


class LoginIn(BaseModel):          # 邮箱+口令登录（RBAC spec §2）
    email: Utf8Str = Field(max_length=255)
    password: Utf8Str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    old_password: Utf8Str = Field(max_length=256)
    new_password: Utf8Str = Field(min_length=8, max_length=256)


class UserIn(BaseModel):
    email: Utf8Str = Field(max_length=255)
    name: Utf8Str = Field(default="", max_length=128)
    password: Utf8Str = Field(min_length=8, max_length=256)
    role: str = Field(default="member", json_schema_extra={"pattern": ROLE_PATTERN})


class UserPatchIn(BaseModel):
    name: Utf8Str | None = Field(None, max_length=128)
    role: str | None = Field(None, json_schema_extra={"pattern": ROLE_PATTERN})
    status: str | None = Field(None, json_schema_extra={"pattern": USER_STATUS_PATTERN})
    # 默认必须显式 None：Field 无默认=必填（同款写法见 ModelPatchIn），否则 patch 不带 password 即 422
    password: Utf8Str | None = Field(None, min_length=8, max_length=256)


class GrantsIn(BaseModel):
    kb_ids: list[ReqId] = []


# 契约 fuzz 前提：真实错误码必须写进 spec，否则 schemathesis 判合法响应为违约
class ErrorOut(BaseModel):
    detail: str


_ERR = lambda code, msg: {code: {"model": ErrorOut, "description": msg}}  # noqa: E731
# fuzz 修复④：请求体不是合法 JSON 时 Starlette 直接回 400（FastAPI 默认 spec 只带 422）——按实声明
_ERR_BODY = _ERR(400, "请求体解析失败")

MISS_ANSWER = "资料里没有相关内容，无法回答。"


class _AllowHeaderMiddleware:
    """fuzz 修复⑤：同一路径由多个单方法路由拼成，Starlette 的 405/OPTIONS 只报首个路由的方法
    （AllowHeaderMismatch 抓到 Allow: POST 少了 GET，违反 RFC 9110）——合并为路径真实方法全集。"""

    def __init__(self, app: ASGIApp, routes) -> None:
        self.app, self.routes = app, routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        methods: set[str] = set()
        if scope["type"] == "http":
            for route in self.routes:
                allowed = getattr(route, "methods", None)
                if allowed:
                    matched = route.matches(scope)
                    match = matched[0] if isinstance(matched, tuple) else matched
                    if match in (Match.FULL, Match.PARTIAL):
                        methods |= allowed
        if not methods:
            await self.app(scope, receive, send)
            return

        async def send_with_allow(message: dict) -> None:
            if message["type"] == "http.response.start" and (
                    message["status"] == 405 or scope["method"] == "OPTIONS"):
                headers = MutableHeaders(raw=message["headers"])
                headers["allow"] = ", ".join(sorted(methods))
            await send(message)

        await self.app(scope, receive, send_with_allow)


def _doc_json(d: Document) -> dict:
    return {"id": d.id, "kb_id": d.kb_id, "name": d.name, "status": d.status,
            "error": d.error, "size_bytes": d.size_bytes}


def create_app(
    *,
    engine: Engine,
    embedder=None,
    chat_fn: Callable[[str, list[dict]], dict] | None = None,
    upload_dir: str = "uploads",
    queue=None,          # ImportQueue 协议；None=同步入库
    mineru=None,         # MinerUClient；None=扫描件解析直接失败并说明原因
    secret: str | None = None,  # 网关主密钥（GATEWAY_SECRET），None=读配置
) -> FastAPI:
    app = FastAPI(title="Umax RAG", version="0.1.0")
    s = get_settings()
    gateway_secret = s.gateway_secret if secret is None else secret

    def get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    # ==================== RBAC（spec 2026-09-23）：账号+DB 会话+库级授权 ====================
    SESSION_COOKIE, SESSION_TTL_DAYS = "umax_session", 7
    throttle = LoginThrottle()   # 每 app 实例独立计数：测试互污染为零

    _ERR_UNAUTH, _ERR_FORBID = _ERR(401, "需要登录"), _ERR(403, "需要管理员权限")

    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def get_user(request: Request, session: Session = Depends(get_session)) -> User:
        """cookie→会话→账号三点一线，任一断 401（禁用的存活会话也断在这）——所有受护端点的统一依赖。"""
        token = request.cookies.get(SESSION_COOKIE)
        row = session.get(UserSession, token_digest(token)) if token else None
        user = session.get(User, row.user_id) if row else None
        if not row or row.expires_at <= _now() or not user or user.status != "active":
            raise HTTPException(401, "需要登录")
        request.state.user, request.state.session_hash = user, row.token_hash
        return user

    def require_admin_role(user: User = Depends(get_user)) -> User:
        if user.role != "admin":
            raise HTTPException(403, "需要管理员权限")
        return user

    def allowed_kb_ids(session: Session, user: User) -> set[int] | None:
        """None=admin 隐式全库；member=授权集合（可为空集——消费端必须区分 None 与 set()）。"""
        if user.role == "admin":
            return None
        return {g.kb_id for g in session.query(UserKbGrant).filter_by(user_id=user.id)}

    @app.post("/api/v1/auth/login", status_code=204,
              responses={**_ERR(401, "邮箱或口令错误"),
                         **_ERR(429, "失败次数过多，15 分钟后再试"), **_ERR_BODY})
    def auth_login(body: LoginIn, request: Request, response: Response,
                   session: Session = Depends(get_session)):
        key = f"{body.email}|{_client_ip(request)}"
        if throttle.blocked(key):
            # 文案与 spec 里 429 的 description 一字对齐（评审收编③）：同一码两处措辞必然漂移
            raise HTTPException(429, "失败次数过多，15 分钟后再试")
        u = session.query(User).filter_by(tenant_id="default", email=body.email).first()
        if not u or not verify_password(body.password, u.hashed_password) or u.status != "active":
            throttle.failure(key)
            audit_record(session, "login_failed", user_email=body.email, ip=_client_ip(request))
            session.commit()   # 审计必须先落，再抛 401
            raise HTTPException(401, "邮箱或口令错误")
        throttle.success(key)
        plain, token_hash = new_session_token()
        session.add(UserSession(token_hash=token_hash, user_id=u.id,
                                expires_at=_now() + timedelta(days=SESSION_TTL_DAYS)))
        audit_record(session, "login_success", user_email=u.email, target_type="user",
                     target_id=u.id, ip=_client_ip(request))
        session.commit()
        # secure 标记留生产硬化：HTTPS 终结在反代后时加 secure=True 即可（评审收编⑤）；
        # 当前 dev/测试是 http://127.0.0.1，加了 cookie 直接被丢弃，全链路登录态失效
        response.set_cookie(SESSION_COOKIE, plain, httponly=True, samesite="lax",
                            max_age=SESSION_TTL_DAYS * 24 * 3600, path="/")

    @app.post("/api/v1/auth/logout", status_code=204, responses=_ERR_UNAUTH)
    def auth_logout(request: Request, response: Response,
                    user: User = Depends(get_user), session: Session = Depends(get_session)):
        row = session.get(UserSession, request.state.session_hash)
        if row:
            session.delete(row)
        audit_record(session, "logout", user_email=user.email, ip=_client_ip(request))
        session.commit()
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.get("/api/v1/auth/me", responses=_ERR_UNAUTH)
    def auth_me(user: User = Depends(get_user), session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        return {"email": user.email, "name": user.name, "role": user.role,
                "kb_ids": None if allowed is None else sorted(allowed)}

    @app.post("/api/v1/auth/change-password", status_code=204,
              # 422 不覆写：FastAPI 默认 422（HTTPValidationError，detail 是数组）——用 _ERR(ErrorOut)
              # 覆写会被 fuzz 判 response_schema_conformance 违约（JSON 解析失败先于依赖 401 发生）。
              # 401 合并顺序（评审收编②）：_ERR_UNAUTH 在前、业务文案在后——同一状态码只有一个
              # description，端点专属的"旧口令错误"必须胜过通用"需要登录"（后者由 detail 承载）。
              responses={**_ERR_UNAUTH, **_ERR(401, "旧口令错误"), **_ERR_BODY})
    def auth_change_password(body: ChangePasswordIn, request: Request,
                             user: User = Depends(get_user), session: Session = Depends(get_session)):
        if not verify_password(body.old_password, user.hashed_password):
            raise HTTPException(401, "旧口令错误")
        user.hashed_password = hash_password(body.new_password)
        session.query(UserSession).filter(
            UserSession.user_id == user.id,
            UserSession.token_hash != request.state.session_hash).delete()   # 踢其他设备，留当前
        audit_record(session, "user_updated", user_email=user.email, target_type="user",
                     target_id=user.id, detail={"fields": ["self_password"]}, ip=_client_ip(request))
        session.commit()

    # ---- 用户管理 + 库级授权（spec §3.1，任务 4）：admin 独占，写操作全审计 ----
    def _grants_map(session: Session) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for g in session.query(UserKbGrant).order_by(UserKbGrant.kb_id):
            out.setdefault(g.user_id, []).append(g.kb_id)
        return out

    def _user_json(u: User, grants: dict[int, list[int]]) -> dict:
        return {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
                "status": u.status, "created_at": u.created_at.isoformat(),
                "kb_ids": None if u.role == "admin" else grants.get(u.id, [])}

    def _revoke_sessions(session: Session, user_id: int, *, except_hash: str | None = None) -> None:
        q = session.query(UserSession).filter_by(user_id=user_id)
        if except_hash:
            q = q.filter(UserSession.token_hash != except_hash)
        q.delete()

    @app.get("/api/v1/users", responses={**_ERR_UNAUTH, **_ERR_FORBID})
    def list_users(admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        g = _grants_map(session)
        return [_user_json(u, g) for u in session.query(User).order_by(User.id)]

    @app.post("/api/v1/users", status_code=201,
              responses={**_ERR(400, "email 重复或 role 非法"), **_ERR_UNAUTH, **_ERR_FORBID, **_ERR_BODY})
    def create_user_api(body: UserIn, request: Request,
                        admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        if body.role not in ROLES:
            raise HTTPException(400, "role 仅支持 admin/member")
        if session.query(User).filter_by(tenant_id="default", email=body.email).first():
            raise HTTPException(400, "该邮箱已存在")
        u = User(tenant_id="default", email=body.email, name=body.name or body.email.split("@")[0],
                 hashed_password=hash_password(body.password), role=body.role, status="active")
        session.add(u)
        session.flush()
        audit_record(session, "user_created", user_email=admin.email, target_type="user",
                     target_id=u.id, detail={"email": u.email, "role": u.role}, ip=_client_ip(request))
        session.commit()
        return _user_json(u, _grants_map(session))

    @app.patch("/api/v1/users/{user_id}",
               responses={**_ERR(400, "不能对当前登录管理员降级/禁用，role/status 非法"),
                          **_ERR(404, "用户不存在"), **_ERR_UNAUTH, **_ERR_FORBID, **_ERR_BODY})
    def patch_user(user_id: PathId, body: UserPatchIn, request: Request,
                   admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.id == admin.id and (body.role == "member" or body.status == "disabled"):
            raise HTTPException(400, "不能对当前登录管理员降级或禁用")
        if body.role and body.role not in ROLES:
            raise HTTPException(400, "role 仅支持 admin/member")
        if body.status and body.status not in USER_STATUSES:
            raise HTTPException(400, "status 仅支持 active/disabled")
        changed: list[str] = []
        for f in ("name", "role", "status"):
            v = getattr(body, f)
            if v is not None and v != getattr(u, f):
                setattr(u, f, v)
                changed.append(f)
        if body.password:
            u.hashed_password = hash_password(body.password)
            changed.append("password")
        if {"role", "status", "password"} & set(changed):
            # 角色/启停/重置口令变更一律吊销（spec §2）；管理员自重置保留当前会话（评审收编⑦）——
            # 与自助改密同语义：吊销的是"其他设备"，不是把操作者从正在做的管理动作里踢出去。
            # 自降级/自禁用在上游已 400，故 except_hash 只会因 password 生效。
            _revoke_sessions(session, u.id,
                             except_hash=request.state.session_hash if u.id == admin.id else None)
        if changed:
            audit_record(session, "user_updated", user_email=admin.email, target_type="user",
                         target_id=u.id, detail={"fields": changed}, ip=_client_ip(request))
        session.commit()
        return _user_json(u, _grants_map(session))

    @app.get("/api/v1/users/{user_id}/grants",
             responses={**_ERR(400, "管理员隐式全库，无授权表"), **_ERR(404, "用户不存在"),
                        **_ERR_UNAUTH, **_ERR_FORBID})
    def get_grants(user_id: PathId, admin: User = Depends(require_admin_role),
                   session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.role == "admin":
            raise HTTPException(400, "管理员隐式全库，无授权表")
        return sorted(_grants_map(session).get(u.id, []))

    @app.put("/api/v1/users/{user_id}/grants",
             responses={**_ERR(400, "管理员无授权表 / 知识库不存在"), **_ERR(404, "用户不存在"),
                        **_ERR_UNAUTH, **_ERR_FORBID, **_ERR_BODY})
    def put_grants(user_id: PathId, body: GrantsIn, request: Request,
                   admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
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
                     target_id=u.id, detail={"kb_ids": ids}, ip=_client_ip(request))
        session.commit()
        return ids

    # ---- 审计查询（spec §5，任务 5）：admin 独占，过滤+分页，倒序 ----
    @app.get("/api/v1/audit", responses={**_ERR_UNAUTH, **_ERR_FORBID})
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

    @app.get("/api/v1/health")
    def health(session: Session = Depends(get_session)):
        session.execute(sa_text("SELECT 1"))
        return {"status": "ok"}

    # ---- 知识库 ----
    def _guard_kb_ids(allowed: set[int] | None, kb_ids: list[int] | None) -> None:
        """显式点了未授权库 → 403（admin 的 allowed 是 None，直通）。"""
        if allowed is not None and not set(kb_ids or []) <= allowed:
            raise HTTPException(403, "无权访问指定知识库")

    @app.post("/api/v1/kb", status_code=201,
              responses={**_ERR_BODY, **_ERR_UNAUTH, **_ERR_FORBID})
    def create_kb(body: KbIn, request: Request,
                  admin: User = Depends(require_admin_role),
                  session: Session = Depends(get_session)):
        kb = KnowledgeBase(tenant_id="default", name=body.name, description=body.description,
                           embedding_model=s.embedding_model, chunk_target=s.chunk_target)
        session.add(kb)
        session.flush()
        audit_record(session, "kb_created", user_email=admin.email,
                     target_type="kb", target_id=kb.id, detail={"name": body.name},
                     ip=_client_ip(request))
        session.commit()
        return {"id": kb.id, "name": kb.name, "description": kb.description}

    @app.get("/api/v1/kb", responses=_ERR_UNAUTH)
    def list_kb(user: User = Depends(get_user), session: Session = Depends(get_session)):
        # member 的库列表在查询层过滤（不是前端隐藏）——空授权=空列表
        allowed = allowed_kb_ids(session, user)
        q = session.query(KnowledgeBase)
        if allowed is not None:
            q = q.filter(KnowledgeBase.id.in_(allowed))
        return [{"id": k.id, "name": k.name, "description": k.description}
                for k in q.order_by(KnowledgeBase.id)]

    @app.get("/api/v1/kb/{kb_id}/documents",
             responses={**_ERR(404, "知识库不存在"), **_ERR_UNAUTH})
    def list_documents(kb_id: PathId, user: User = Depends(get_user),
                       session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        # 不在授权范围内的库与"不存在"同文案：探测不出别人的库 id 存不存在
        if not session.get(KnowledgeBase, kb_id) or (allowed is not None and kb_id not in allowed):
            raise HTTPException(404, "知识库不存在")
        return [_doc_json(d) for d in session.query(Document)
                .filter_by(kb_id=kb_id).order_by(Document.id)]

    # ---- 文档与入库 ----
    @app.post("/api/v1/kb/{kb_id}/documents", status_code=201,
              responses={**_ERR(404, "知识库不存在"), **_ERR(415, "不支持的文件类型"),
                         **_ERR_BODY, **_ERR_UNAUTH, **_ERR_FORBID})
    def upload_document(kb_id: PathId, request: Request, file: UploadFile = File(...),
                        admin: User = Depends(require_admin_role),
                        session: Session = Depends(get_session)):
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        # 修复⑧收口：multipart filename 不经 pydantic 验证链，是唯一的裸入口字符串——
        # 取 basename（防目录注入）+ 过 NUL/孤立代理清洗闸 + 截 200（_doc_json 会回显给前端），
        # 否则 \x00 直接进 Path 拼接/write_bytes/PG 即未声明 500
        name = _clean_text(Path(file.filename or "unnamed").name)[:200]
        if not supported_ext(name):
            raise HTTPException(415, f"暂不支持的文件类型：{name}（一期 .txt/.md，MinerU 接入后支持 PDF/Office）")
        raw = file.file.read()
        doc = Document(tenant_id="default", kb_id=kb_id, name=name, status="pending",
                       size_bytes=len(raw), mime=file.content_type)
        session.add(doc)
        session.flush()
        p = Path(upload_dir) / f"{uuid4().hex[:8]}_{name}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        doc.storage_path = str(p)
        audit_record(session, "document_uploaded", user_email=admin.email,
                     target_type="document", target_id=doc.id,
                     detail={"kb_id": kb_id, "name": name}, ip=_client_ip(request))
        session.commit()
        if queue is not None:
            queue.enqueue_import(doc.id)
            return _doc_json(doc)  # pending，worker 接手
        doc = ingest_document(session, doc, raw, embedder=embedder, mineru=mineru,
                              chunk_target=s.chunk_target, chunk_min=s.chunk_min)
        return _doc_json(doc)

    def _visible_doc(session: Session, user: User, doc_id: int) -> Document:
        """按库级授权取文档：不可见（不存在/在未授权库）统一 404 同文案，探测不出差异。"""
        doc = session.get(Document, doc_id)
        allowed = allowed_kb_ids(session, user)
        if not doc or (allowed is not None and doc.kb_id not in allowed):
            raise HTTPException(404, "文档不存在")
        return doc

    @app.get("/api/v1/documents/{doc_id}",
             responses={**_ERR(404, "文档不存在"), **_ERR_UNAUTH})
    def get_document(doc_id: PathId, user: User = Depends(get_user),
                     session: Session = Depends(get_session)):
        return _doc_json(_visible_doc(session, user, doc_id))

    @app.get("/api/v1/documents/{doc_id}/chunks",
             responses={**_ERR(404, "文档不存在"), **_ERR_UNAUTH})
    def preview_chunks(doc_id: PathId, user: User = Depends(get_user),
                       session: Session = Depends(get_session)):
        _visible_doc(session, user, doc_id)
        return [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content,
                 "has_embedding": c.embedding is not None}
                for c in session.query(Chunk).filter_by(document_id=doc_id)
                .order_by(Chunk.chunk_index)]

    @app.patch("/api/v1/documents/{doc_id}",
               responses={**_ERR(404, "文档不存在"), **_ERR_BODY,
                          **_ERR_UNAUTH, **_ERR_FORBID})
    def patch_document(doc_id: PathId, body: DocPatchIn,
                       admin: User = Depends(require_admin_role),
                       session: Session = Depends(get_session)):
        # 有意不记审计（spec §5 / 任务 5 表）：状态机内部推进（worker 回写 pending/ready/failed），
        # 不是人工写操作——记了只会淹没真实操作事件
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        if body.status:
            doc.status = body.status
        doc.error = body.error
        session.commit()
        return _doc_json(doc)

    @app.post("/api/v1/documents/{doc_id}/reprocess", status_code=202,
              responses={**_ERR(404, "文档不存在"), **_ERR_UNAUTH, **_ERR_FORBID})
    def reprocess(doc_id: PathId, request: Request,
                  admin: User = Depends(require_admin_role),
                  session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        # 审计只 add 不 commit：队列分支随下面的 commit 落库，同步分支随 ingest_document
        # 内部的 commit 落库——两条路径都是"操作真发生了才有事件"
        audit_record(session, "document_reprocessed", user_email=admin.email,
                     target_type="document", target_id=doc.id, detail={"kb_id": doc.kb_id},
                     ip=_client_ip(request))
        if queue is not None:
            doc.status, doc.error = "pending", None
            session.commit()
            queue.enqueue_import(doc.id)
            return _doc_json(doc)
        doc = ingest_document(session, doc, read_stored(doc), embedder=embedder,
                              mineru=mineru, chunk_target=s.chunk_target,
                              chunk_min=s.chunk_min)
        return _doc_json(doc)

    # ---- 检索与问答：可见性在检索层钳制（SQL 谓词），命中集就是授权集的子集 ----
    @app.post("/api/v1/retrieve", responses={**_ERR_BODY, **_ERR_UNAUTH, **_ERR_FORBID})
    def retrieve_api(body: RetrieveIn, user: User = Depends(get_user),
                     session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        _guard_kb_ids(allowed, body.kb_ids)
        return retrieve(session, body.query, embedder=embedder, kb_ids=body.kb_ids,
                        allowed_kb_ids=allowed,
                        recall_k=s.recall_k, top_k=body.top_k or s.rerank_top_n,
                        min_sim=s.min_sim)

    @app.post("/api/v1/chat", responses={**_ERR_BODY, **_ERR_UNAUTH, **_ERR_FORBID})
    def chat(body: ChatIn, user: User = Depends(get_user),
             session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        _guard_kb_ids(allowed, body.kb_ids)
        hits = retrieve(session, body.question, embedder=embedder, kb_ids=body.kb_ids,
                        allowed_kb_ids=allowed,
                        recall_k=s.recall_k, top_k=s.rerank_top_n, min_sim=s.min_sim)
        conv = session.get(Conversation, body.conversation_id) if body.conversation_id else None
        if conv is not None and conv.user_email != user.email:
            raise HTTPException(404, "会话不存在")   # 跨用户会话与不存在同文案（同 list_messages）
        if conv is None:
            conv = Conversation(tenant_id="default", user_email=user.email,
                                title=body.question[:32], kb_ids=body.kb_ids or [])
            session.add(conv)
            session.flush()
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="user",
                            content=[{"type": "text", "text": body.question}]))

        if not hits or chat_fn is None:
            answer, usage, citations = MISS_ANSWER, {"prompt_tokens": 0, "completion_tokens": 0}, []
        else:
            out = chat_fn(body.question, hits)
            answer = out["answer"]
            usage = {"prompt_tokens": out.get("prompt_tokens") or 0,
                     "completion_tokens": out.get("completion_tokens") or 0}
            citations = [{"n": i, "doc_name": h["doc_name"], "chunk_id": h["id"],
                          "excerpt": h["content"][:80]} for i, h in enumerate(hits, 1)]
            # 记账单点在端点：网关 make_chat_fn 已交回记账职责（logged 约定退役），
            # 这里必记且只记一次，台账归真实登录者而非占位邮箱
            session.add(UsageRecord(tenant_id="default", user_email=user.email,
                                    scenario="chat", model=out.get("model") or s.chat_model,
                                    prompt_tokens=usage["prompt_tokens"],
                                    completion_tokens=usage["completion_tokens"],
                                    latency_ms=out.get("latency_ms")))
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="assistant",
                            content=[{"type": "text", "text": answer}], citations=citations))
        session.commit()
        return {"conversation_id": conv.id, "answer": answer, "citations": citations,
                "cited_docs": parse_citations(answer, hits), "usage": usage}

    # ---- 会话历史：按登录者隔离（admin 也没有特权看别人的会话）----
    @app.get("/api/v1/conversations", responses=_ERR_UNAUTH)
    def list_conversations(user: User = Depends(get_user), session: Session = Depends(get_session)):
        return [{"id": c.id, "title": c.title, "kb_ids": c.kb_ids}
                for c in session.query(Conversation).filter(Conversation.user_email == user.email)
                .order_by(Conversation.id)]

    @app.get("/api/v1/conversations/{conv_id}/messages",
             responses={**_ERR(404, "会话不存在"), **_ERR_UNAUTH})
    def list_messages(conv_id: PathId, user: User = Depends(get_user),
                      session: Session = Depends(get_session)):
        conv = session.get(Conversation, conv_id)
        if not conv or conv.user_email != user.email:
            raise HTTPException(404, "会话不存在")
        return [{"id": m.id, "role": m.role, "content": m.content, "citations": m.citations}
                for m in session.query(Message).filter_by(conversation_id=conv_id)
                .order_by(Message.id)]

    # ---- 模型后台（§C：改表即生效；key 加密存储、打码、不回传明文）----
    def _require_secret() -> str:
        if not gateway_secret:
            raise HTTPException(503, "未配置主密钥 GATEWAY_SECRET，无法管理模型 key")
        return gateway_secret

    def _model_json(m: ModelConfig) -> dict:
        plain = decrypt_secret(m.encrypted_api_key, _require_secret())
        return {"id": m.id, "scenario": m.scenario, "provider": m.provider,
                "base_url": m.base_url, "model_name": m.model_name,
                "capabilities": m.capabilities, "is_default": m.is_default,
                "fallback_rank": m.fallback_rank, "enabled": m.enabled,
                "api_key_masked": ("****" + plain[-4:]) if plain else ""}

    @app.post("/api/v1/models", status_code=201,
              responses={**_ERR(400, "scenario 非法"), **_ERR(503, "未配置 GATEWAY_SECRET"),
                         **_ERR_UNAUTH, **_ERR_FORBID})
    def create_model(body: ModelIn, request: Request,
                     admin: User = Depends(require_admin_role),
                     session: Session = Depends(get_session)):
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, f"scenario 仅支持 {'/'.join(sorted(SCENARIOS))}")
        m = ModelConfig(tenant_id="default", scenario=body.scenario, provider=body.provider,
                        base_url=body.base_url, model_name=body.model_name,
                        encrypted_api_key=encrypt_secret(body.api_key, _require_secret()),
                        capabilities=body.capabilities, is_default=body.is_default,
                        fallback_rank=body.fallback_rank, enabled=body.enabled)
        session.add(m)
        session.flush()
        # detail 只进非敏感定位字段：api_key/base_url/provider 明文一律不入审计
        audit_record(session, "model_created", user_email=admin.email,
                     target_type="model", target_id=m.id,
                     detail={"scenario": body.scenario, "model_name": body.model_name,
                             "fallback_rank": body.fallback_rank}, ip=_client_ip(request))
        session.commit()
        return _model_json(m)

    @app.get("/api/v1/models", responses={**_ERR_UNAUTH, **_ERR_FORBID})
    def list_models(admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        rows = session.query(ModelConfig).order_by(ModelConfig.scenario,
                                                   ModelConfig.fallback_rank, ModelConfig.id)
        return [_model_json(m) for m in rows]

    @app.patch("/api/v1/models/{model_id}",
               responses={**_ERR(400, "scenario 非法"), **_ERR(404, "模型配置不存在"),
                          **_ERR(503, "未配置 GATEWAY_SECRET"),
                          **_ERR_UNAUTH, **_ERR_FORBID})
    def patch_model(model_id: PathId, body: ModelPatchIn, request: Request,
                    admin: User = Depends(require_admin_role),
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
            data["encrypted_api_key"] = encrypt_secret(data.pop("api_key"), _require_secret())
        for k, v in data.items():
            setattr(m, k, v)
        if fields:   # 无变更不记审计（评审收编⑧/⑩ 统一裁定）：空 PATCH 是"什么都没改"，
            # 记一条 fields=[] 的事件只会污染事件流——与 patch_user 的 `if changed:` 同一口径
            audit_record(session, "model_updated", user_email=admin.email,
                         target_type="model", target_id=m.id, detail={"fields": fields},
                         ip=_client_ip(request))
        session.commit()
        return _model_json(m)

    @app.delete("/api/v1/models/{model_id}", status_code=204,
                responses={**_ERR(503, "未配置 GATEWAY_SECRET"), **_ERR_UNAUTH, **_ERR_FORBID})
    def delete_model(model_id: PathId, request: Request,
                     admin: User = Depends(require_admin_role),
                     session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if m:
            model_name = m.model_name      # 删前取：行没了就查不到被删的是哪个模型
            session.delete(m)
            audit_record(session, "model_deleted", user_email=admin.email,
                         target_type="model", target_id=model_id,
                         detail={"model_name": model_name}, ip=_client_ip(request))
            session.commit()

    # ---- 用量看板简版（§C：成本折算的事实来源）----
    @app.get("/api/v1/usage/summary", responses={**_ERR_UNAUTH, **_ERR_FORBID})
    def usage_summary(admin: User = Depends(require_admin_role),
                      session: Session = Depends(get_session)):
        rows = (session.query(UsageRecord.scenario, UsageRecord.model,
                              func.count().label("calls"),
                              func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
                              func.coalesce(func.sum(UsageRecord.completion_tokens), 0))
                .group_by(UsageRecord.scenario, UsageRecord.model).all())
        return [{"scenario": r[0], "model": r[1], "calls": r[2],
                 "prompt_tokens": int(r[3]), "completion_tokens": int(r[4])} for r in rows]

    app.add_middleware(_AllowHeaderMiddleware, routes=app.router.routes)
    return app


def build_production_app(upload_dir: str = "uploads",
                         engine: Engine | None = None) -> FastAPI:
    """真依赖装配：配了 GATEWAY_SECRET 且表里有对应场景模型 → 走网关（后台改表即生效）；
    否则退回 .env 里的百炼直连（阶段 0 链路，冒烟可跑）。"""
    from sqlalchemy import create_engine

    from app.db.base import Base
    from app.services.chat import ChatClient, make_chat_fn
    from app.services.embeddings import BailianEmbedder
    from app.services.parsers import MinerUClient

    s = get_settings()
    if engine is None:
        engine = create_engine(s.sqlalchemy_url(), pool_pre_ping=True)
        import app.models  # noqa: F401  一键部署：启动即建表（正式迁移方案后续以 Alembic 接管）
        Base.metadata.create_all(engine)
    # 老库一键升级的过渡 pragmatics（无 Alembic）：create_all 不给已存在的表加列，
    # users.name/status 是任务 2 新增列——幂等 ADD COLUMN IF NOT EXISTS 补齐（fresh 库同样通过）
    with engine.begin() as con:
        con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                            "name VARCHAR(128) NOT NULL DEFAULT ''"))
        con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                            "status VARCHAR(16) NOT NULL DEFAULT 'active'"))
    # 播种初始管理员（spec §2）：users 空表时按 ADMIN_EMAIL/ADMIN_PASSWORD 落一条 role=admin，
    # 口令 hash 后入库（明文永不落库）。测试装配 create_app 不播种，走 conftest.seed_user——
    # 故这里只在 build_production_app 里做，且放在 engine 判定之后（注入 engine 也要播种）。
    with Session(engine) as ses:   # ses 而非 s：s 已是 Settings，with 目标名会覆盖函数作用域
        if ses.query(User).first() is None:
            ses.add(User(tenant_id="default", email=s.admin_email, name="admin",
                         hashed_password=hash_password(s.admin_password), role="admin"))
            ses.commit()
            import logging
            logging.getLogger("umax").warning(
                "已播种初始管理员 %s（ADMIN_EMAIL/ADMIN_PASSWORD）——部署后立即登录改密", s.admin_email)
    chat_fn = embedder = None
    if s.gateway_secret:
        from app.services.gateway import ModelGateway

        gw = ModelGateway(engine, secret=s.gateway_secret)
        if gw.providers("chat"):
            chat_fn = gw.make_chat_fn()
        if gw.providers("embedding"):
            embedder = gw.make_embedder()
    if (chat_fn is None or embedder is None) and s.dashscope_api_key:
        chat_fn = chat_fn or make_chat_fn(ChatClient(api_key=s.dashscope_api_key,
                                                     base_url=s.dashscope_compat_base,
                                                     model=s.chat_model))
        embedder = embedder or BailianEmbedder(api_key=s.dashscope_api_key,
                                               base_url=s.dashscope_compat_base,
                                               model=s.embedding_model,
                                               dimensions=s.embedding_dim)
    mineru = MinerUClient(s.mineru_base_url) if s.mineru_base_url else None
    queue = None
    if s.queue_backend == "arq":
        from app.queue import ArqQueue

        queue = ArqQueue(redis_host=s.redis_host, redis_port=s.redis_port)
    if not s.admin_email or not s.admin_password:
        import logging

        logging.getLogger("umax").warning(
            "ADMIN_EMAIL/ADMIN_PASSWORD 未配置：将按默认账号播种初始管理员，部署后必须登录改密")
    return create_app(engine=engine, embedder=embedder, chat_fn=chat_fn,
                      upload_dir=upload_dir, mineru=mineru, queue=queue)


def main() -> None:
    import uvicorn

    uvicorn.run(build_production_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
