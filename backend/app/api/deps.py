# 运行时依赖（原 create_app 里那些闭包自由变量的家）+ FastAPI 依赖函数。
#
# 拆 router 前，这些名字都是 create_app 的闭包变量，端点函数直接就能用；拆开后端点成了
# 另一个模块里的函数，取值就只能走 app.state——`get_runtime(request)` 是唯一的入口。
# 好处不只是"能拆开"：依赖现在可以被单独 import 与测试，且**一个 app 实例一份**
# （throttle 就是靠这个做到的"测试互污染为零"）。
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api.constants import MUST_CHANGE_EXEMPT, SESSION_COOKIE
from app.models import KnowledgeBase, UsageRecord, User, UserKbGrant, UserSession
from app.services.auth import token_digest
from app.services.license import LicenseStatus, load_license_status
from app.services.settings import SettingsStore


def now() -> datetime:
    return datetime.now(timezone.utc)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def allowed_kb_ids(session: Session, user: User) -> set[int] | None:
    """None=admin 隐式全库；member=授权集合（可为空集——消费端必须区分 None 与 set()）。"""
    if user.role == "admin":
        return None
    return {g.kb_id for g in session.query(UserKbGrant).filter_by(user_id=user.id)}


def guard_kb_ids(allowed: set[int] | None, kb_ids: list[int] | None) -> None:
    """显式点了未授权库 → 403（admin 的 allowed 是 None，直通）。"""
    if allowed is not None and not set(kb_ids or []) <= allowed:
        raise HTTPException(403, "无权访问指定知识库")


@dataclass
class Runtime:
    """一个 app 实例的全部运行时协作者（原 create_app 闭包里的自由变量逐个搬到这里）。

    分成两类：字段是"注进来的依赖"（engine/embedder/queue/…），方法是"用这些依赖算出来的
    共享口径"（配额、切块参数、API key 鉴权…）。方法而不是散函数，是因为它们天然属于
    同一个实例——跨 router 复用时不必再各自传一遍依赖。
    """
    engine: Engine
    settings: Any                       # Settings（.env）
    cfg: SettingsStore                  # 配置中心（后台可改的参数）
    gateway_secret: str | None
    spawn: Callable[[Callable[[], None]], None]
    rerank: Callable | None
    embedder: Any = None
    chat_fn: Any = None
    vision_fn: Any = None
    judge_fn: Any = None
    upload_dir: str = "uploads"
    queue: Any = None
    mineru: Any = None
    throttle: Any = None
    license_public_key: str | None = None
    license_file: Path | None = None
    license_fingerprint: str | None = None
    started_at: str = ""
    settings_put_model: Any = None      # 按 SPEC 动态建模的配置中心请求体

    # ---- License 授权（§D）：每请求现读文件——续期换文件立即生效、签名即信任根 ----
    def license_status(self) -> LicenseStatus:
        return load_license_status(self.license_file, self.license_public_key,
                                   fingerprint=self.license_fingerprint)

    def require_secret(self) -> str:
        if not self.gateway_secret:
            raise HTTPException(503, "未配置主密钥 GATEWAY_SECRET，无法管理模型 key")
        return self.gateway_secret

    # ---- 配置中心的两处共享口径 ----
    def chunk_params(self, kb: KnowledgeBase | None) -> dict:
        """切块参数：一律走配置中心那份单一事实源（sync/async 必须同口径）。"""
        return self.cfg.chunk_params(kb)

    def quota_state(self, session: Session, user: User) -> dict:
        """当日/当月已用 token 与限额。窗口按 UTC 自然日/自然月（与台账口径一致）。"""
        t = now()
        day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        daily, monthly = (_period_used(session, user.email, day_start),
                          _period_used(session, user.email, month_start))
        ratio = float(self.cfg.effective()["quota_warn_ratio"])
        d_lim, m_lim = user.daily_token_limit, user.monthly_token_limit
        exceeded = (d_lim is not None and daily >= d_lim) or (m_lim is not None and monthly >= m_lim)
        near = (not exceeded) and (
            (d_lim is not None and d_lim > 0 and daily >= d_lim * ratio)
            or (m_lim is not None and m_lim > 0 and monthly >= m_lim * ratio))
        return {"daily_used": daily, "daily_limit": d_lim,
                "monthly_used": monthly, "monthly_limit": m_lim,
                "near_limit": near, "exceeded": exceeded, "warn_ratio": ratio}

    def enforce_quota(self, session: Session, user: User) -> None:
        """超限即拦（429），且拦在检索与调模型之前——闸门的意义是不白花钱。"""
        st = self.quota_state(session, user)
        if st["exceeded"]:
            if st["daily_limit"] is not None and st["daily_used"] >= st["daily_limit"]:
                raise HTTPException(429, f"今日 token 配额已用尽（{st['daily_used']}/"
                                         f"{st['daily_limit']}），请明日再试或联系管理员调整")
            raise HTTPException(429, f"本月 token 配额已用尽（{st['monthly_used']}/"
                                     f"{st['monthly_limit']}），请联系管理员调整")

    # ---- 开放 API（§2.2）：会话之外的第二条鉴权通路 ----
    def valid_kb_ids(self, session: Session, kb_ids: list[int] | None) -> list[int] | None:
        if kb_ids is None:
            return None
        ids = sorted(set(kb_ids))
        if ids and len(session.query(KnowledgeBase.id)
                      .filter(KnowledgeBase.id.in_(ids)).all()) != len(ids):
            raise HTTPException(400, "存在不存在的知识库 id")
        return ids

    def bearer_key(self, request: Request, session: Session):
        from app.models import ApiKey

        auth = request.headers.get("authorization") or ""
        raw = auth[7:] if auth.startswith("Bearer ") else ""
        row = (session.query(ApiKey).filter_by(key_hash=token_digest(raw)).first()
               if raw else None)
        if not row or not row.enabled:
            raise HTTPException(401, "无效的 API key")
        return row

    def month_used_tokens(self, session: Session, key_id: int) -> int:
        month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = session.query(
            func.sum(UsageRecord.prompt_tokens + UsageRecord.completion_tokens)).filter(
            UsageRecord.user_email == f"apikey:{key_id}",
            UsageRecord.created_at >= month_start).scalar()
        return int(total or 0)


def _period_used(session: Session, email: str, since: datetime) -> int:
    total = session.query(
        func.coalesce(func.sum(UsageRecord.prompt_tokens + UsageRecord.completion_tokens), 0)
    ).filter(UsageRecord.user_email == email, UsageRecord.created_at >= since).scalar()
    return int(total or 0)


# ---- FastAPI 依赖 ----
def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def get_session(request: Request) -> Iterator[Session]:
    with Session(get_runtime(request).engine) as session:
        yield session


def get_user(request: Request, session: Session = Depends(get_session)) -> User:
    """cookie→会话→账号三点一线，任一断 401（禁用的存活会话也断在这）——所有受护端点的统一依赖。
    首登强改密：口令非本人设定（must_change_password）未改密前，除 auth 三件套外一律 428。"""
    token = request.cookies.get(SESSION_COOKIE)
    row = session.get(UserSession, token_digest(token)) if token else None
    user = session.get(User, row.user_id) if row else None
    if not row or row.expires_at <= now() or not user or user.status != "active":
        raise HTTPException(401, "需要登录")
    if user.must_change_password and request.url.path not in MUST_CHANGE_EXEMPT:
        raise HTTPException(428, "首次登录必须修改初始口令")
    request.state.user, request.state.session_hash = user, row.token_hash
    return user


def require_admin_role(user: User = Depends(get_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_license(request: Request, admin: User = Depends(require_admin_role)) -> User:
    """写操作门闸：授权失效即只读（读与问答照常，改密不受影响）——续费压力落在管理动作上。"""
    st = get_runtime(request).license_status()
    if st.enforced and not st.valid:
        raise HTTPException(403, f"授权不可用：{st.reason}")
    return admin
