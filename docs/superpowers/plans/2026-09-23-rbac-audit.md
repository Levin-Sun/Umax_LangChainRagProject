# 阶段 2 · 安全与身份（RBAC + 审计）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 命名说明：spec §4 写 `app/core/security.py`，本计划落位 `backend/app/services/auth.py`——仓库既有惯例是服务件进 `services/`（crypto.py/embeddings.py 同级），core/ 只放 config；此为有意的镜像偏移，评审时按 services 对齐。

**Goal:** 用真实账号体系（全员登录、角色+库级授权、检索层过滤、DB 会话、审计日志）整体替换阶段 1 的共享 ADMIN_TOKEN 方案。

**Architecture:** 后端在 `create_app` 装配内新增 auth 依赖链（cookie→`user_sessions`→`users`→角色/授权），权限过滤以 SQL 谓词下沉到检索内核与列表端点；审计与写操作同事务落 `audit_logs`。前端以 `AuthProvider`（`GET /auth/me` 为唯一真相源）驱动登录页/Nav/门闸/新管理页。

**Tech Stack:** FastAPI + SQLAlchemy + pgvector（真 PG 测试库）、schemathesis 契约 fuzz、`hashlib.scrypt`（stdlib 零新依赖）、Next.js 15 + `@umax/sdk-ts` 契约客户端、Vitest+RTL。

**Spec:** `docs/superpowers/specs/2026-09-23-rbac-audit-design.md`（执行时先通读；spec 是裁决依据，本计划是论证）

## Global Constraints

- 全程 TDD：每个行为先写失败测试（红）→ 最小实现（绿）→ 重构；不许先码后测。
- 契约四步工作流（每个动端点的任务必走）：先测试 → 后端实现 → `cd backend && ../.venv/Scripts/python.exe scripts/export_openapi.py` → `cd sdk-ts && npm test`（gen→新鲜度闸→tsc），**同一提交内**入库 `contracts/openapi.json` 与重新生成的 `schema.d.ts`。
- 前端禁裸 fetch `/api/v1`，一律经 `@umax/sdk-ts`；DTO 断言收口在 `frontend/src/lib/`。
- 后端回归：`cd backend && ../.venv/Scripts/python.exe -m pytest`（需 PG 容器 `umaxrag-pg` 在跑；smoke 另跑 `pytest -m smoke`，真 key 走 `backend/.env`）。
- 前端门禁：`cd frontend && npm test` + `npm run typecheck`；**dev server 在跑时禁止 `npm run build`**（.next 互踩）。
- UI 硬规范：新页面必须过 `src/__tests__/styleGuard.test.ts`（禁默认字号/`text-[...]`/`leading-*`/700+ 字重；字号行高只能来自五级字阶 `text-h1/h2/h3/body/caption`；文字色只用 `--ink-1/2/3` 语义层级）。
- **不得变更 `GATEWAY_SECRET`**（现网 `model_configs` 已有密文）；`.env` 永不入库。
- 口令/密钥明文永不入审计 detail；错误文案不区分"邮箱不存在/口令错/已禁用"（防枚举）。
- 提交信息中文、`feat:/fix:/test:/docs:` 前缀，沿用仓库风格；commit 与 push 分开等用户口令。

## File Structure（改动地图）

```
backend/app/services/auth.py        新：scrypt 哈希/token/登录限流（纯函数+小类）
backend/app/services/audit.py       新：record() 审计写入口 + ACTIONS 常量
backend/app/models/__init__.py      改：User +name/status；新表 UserSession/AuditLog/UserKbGrant
backend/app/core/config.py          改：+admin_email/admin_password；任务 6 删 admin_token
backend/app/services/retrieval.py   改：retrieve() +allowed_kb_ids 谓词钳制
backend/app/services/gateway.py     改：make_chat_fn 不再记账（logged 约定退役）
backend/app/main.py                 大改：auth 依赖链+四 auth 端点（任务 3）→ users/grants/audit 端点（任务 4/5）
                                    → 全端点登录/角色/范围收紧+旧 token 方案退役（任务 6）
backend/tests/conftest.py           改：seed_user / login 助手（任务 3）
backend/tests/test_auth_service.py  新（任务 1） test_rbac_models.py 新（任务 2）
backend/tests/test_auth_api.py      新（任务 3） test_users_api.py 新（任务 4）
backend/tests/test_audit_api.py     新（任务 5） test_permissions.py 新（任务 6）
backend/tests/test_admin_auth.py    任务 6 删除（价值并入 test_permissions + test_contract_declared）
backend/tests/test_{api,chat,models_api,contract_fixes,contract_declared,contract,smoke_api}.py  任务 6 适配登录
backend/tests/test_gateway.py       任务 6 适配 logged 约定删除
contracts/openapi.json / sdk-ts/src/schema.d.ts   各端点任务随提交重导/再生成

frontend/src/lib/auth.tsx           新：AuthProvider/useAuth（/auth/me 真相源）
frontend/src/lib/{paths,api,types}.ts       改：auth/users/audit 路径、删 admin_hint 助手
frontend/src/components/LoginCard.tsx  改：email+口令、角色分流
frontend/src/components/Nav.tsx       改：me 驱动显隐/用户名/退出/改密入口
frontend/src/components/AdminGate.tsx 改：me 驱动（admin_hint 退役）
frontend/src/components/ChatApp.tsx   改：未登录跳登录页、会话按人
frontend/src/components/UsersAdmin.tsx / AuditAdmin.tsx + 两个 page.tsx   新（任务 8）
frontend/src/components/__tests__/*   改+新
```

---

### Task 1: 认证原语 `app/services/auth.py`（scrypt/token/限流）

**Files:**
- Create: `backend/app/services/auth.py`
- Test: `backend/tests/test_auth_service.py`

**Interfaces:**
- Consumes: 无（纯 stdlib）
- Produces: `hash_password(password: str) -> str`、`verify_password(password: str, stored: str) -> bool`、`token_digest(plain: str) -> str`（SHA-256 hex 64 字符）、`new_session_token() -> tuple[str, str]`（明文, token_digest(明文)）、`class LoginThrottle(max_failures=10, window_s=900)`：`blocked(key)/failure(key)/success(key)`——任务 3/6 的 auth 端点消费。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_auth_service.py
# 认证原语：scrypt 往返、篡改/坏格式安全拒绝、会话 token 对、登录限流窗口
import re
from app.services.auth import (LoginThrottle, hash_password, new_session_token,
                               token_digest, verify_password)


def test_hash_roundtrip_and_wrong_password():
    stored = hash_password("S3cret-Pass!")
    assert verify_password("S3cret-Pass!", stored)
    assert not verify_password("wrong", stored)


def test_hash_is_salted_and_format_self_describing():
    a, b = hash_password("same"), hash_password("same")
    assert a != b, "盐必须随机——同口令两次哈希不同"
    assert re.fullmatch(r"scrypt\$16384\$8\$1\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+", a)


def test_verify_rejects_tampered_and_garbage_hashes():
    stored = hash_password("good-pass-123")
    assert not verify_password("whatever", stored[:-5] + "XXXXX")
    for junk in ("", "not-a-hash", "scrypt$16384$8$1$xx", "md5$8$1$aaaa$bbbb"):
        assert not verify_password(junk, junk)


def test_new_session_token_pair():
    plain, digest = new_session_token()
    assert len(plain) >= 32 and plain != digest
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == token_digest(plain), "落库摘要 = 对明文重算，get_user 依赖此一致性"


def test_throttle_blocks_after_10_failures_and_resets_on_success():
    t = LoginThrottle(max_failures=10, window_s=900)
    for _ in range(9):
        t.failure("a@x.com|1.2.3.4")
        assert not t.blocked("a@x.com|1.2.3.4")
    t.failure("a@x.com|1.2.3.4")
    assert t.blocked("a@x.com|1.2.3.4")
    assert not t.blocked("b@x.com|1.2.3.4"), "键=邮箱|IP，互不牵连"
    t.success("a@x.com|1.2.3.4")
    assert not t.blocked("a@x.com|1.2.3.4"), "成功登录清零"


def test_throttle_window_expiry():
    t = LoginThrottle(max_failures=2, window_s=100)
    now = [1000.0]                          # 时钟可注入，窗口过期不靠真等
    for _ in range(2):
        t.failure("k", now=now[0])
    assert t.blocked("k", now=now[0])
    assert not t.blocked("k", now=now[0] + 101)
```

- [ ] **Step 2: 跑红** — `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_auth_service.py -v`，Expected: `ModuleNotFoundError: app.services.auth`（collection error 即红）。

- [ ] **Step 3: 最小实现**

```python
# 认证原语：scrypt 口令哈希（stdlib 零新依赖）/ 会话 token 对 / 登录限流（进程内，单机够用）
import base64
import hashlib
import hmac
import secrets
import time

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    # 任何格式问题都判 False（不抛）：坏哈希行不应把请求变成 500
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        want = base64.b64decode(hash_b64)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                             n=int(n), r=int(r), p=int(p), dklen=len(want))
        return hmac.compare_digest(got, want)
    except Exception:
        return False


def token_digest(plain: str) -> str:
    """会话 token 的落库形态：只存 SHA-256 hex，DB 泄露造不出可用 cookie。"""
    return hashlib.sha256(plain.encode()).hexdigest()


def new_session_token() -> tuple[str, str]:
    """(cookie 明文, 落库摘要)。"""
    plain = secrets.token_urlsafe(32)
    return plain, token_digest(plain)


class LoginThrottle:
    """同 key（email|ip）窗口内连续失败达上限即拒；成功清零。可注入 now（monotonic 秒）。"""

    def __init__(self, *, max_failures: int = 10, window_s: float = 900):
        self.max_failures, self.window_s = max_failures, window_s
        self._fails: dict[str, list[float]] = {}

    def blocked(self, key: str, *, now: float | None = None) -> bool:
        return len(self._recent(key, now if now is not None else time.monotonic())) >= self.max_failures

    def failure(self, key: str, *, now: float | None = None) -> None:
        t = now if now is not None else time.monotonic()
        self._fails[key] = self._recent(key, t) + [t]

    def success(self, key: str) -> None:
        self._fails.pop(key, None)

    def _recent(self, key: str, now: float) -> list[float]:
        stamps = [x for x in self._fails.get(key, []) if now - x < self.window_s]
        if stamps:
            self._fails[key] = stamps
        return stamps
```

- [ ] **Step 4: 跑绿** — 同上命令，Expected: 6 passed。再 `../.venv/Scripts/python.exe -m pytest` 全量不回归。
- [ ] **Step 5: Commit** — `git add backend/app/services/auth.py backend/tests/test_auth_service.py && git commit -m "feat(rbac-1/9): 认证原语——scrypt 哈希/token 对/登录限流（TDD，零新依赖）"`

---

### Task 2: 数据模型——User 扩展 + 三新表 + 配置项

**Files:**
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/core/config.py`（Settings 增两字段；**admin_token 此任务不动**，任务 6 退役）
- Test: `backend/tests/test_rbac_models.py`

**Interfaces:**
- Consumes: `Base/T()/J()` 既有模式；conftest `engine/db` 夹具（drop-create + 函数级 TRUNCATE 自动覆盖新表）
- Produces: ORM 类 `User`（新列 `name:str`、`status:str 默认"active"`）、`UserSession(token_hash PK, user_id, created_at, expires_at)`、`AuditLog`、`UserKbGrant(user_id, kb_id)`；`Settings.admin_email="admin@umax.local"`、`Settings.admin_password="umax-admin-dev"`——任务 3~6 全部消费。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_rbac_models.py
# RBAC 新表落库往返与约束（真 PG；engine/db 夹具自动覆盖 sorted_tables）
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditLog, KnowledgeBase, User, UserKbGrant, UserSession
from app.services.auth import hash_password  # 任务 1 产物；本测试只借它造行，不测它


def _mk_user(db: Session, email="u@x.com", role="member"):
    u = User(tenant_id="default", email=email, name=email.split("@")[0],
             hashed_password=hash_password("x"), role=role)
    db.add(u)
    db.commit()
    return u


def test_user_new_columns_defaults(db: Session):
    u = _mk_user(db)
    got = db.get(User, u.id)
    assert got.name == "u" and got.status == "active" and got.role == "member"


def test_user_session_and_grant_roundtrip(db: Session):
    u = _mk_user(db)
    kb = KnowledgeBase(tenant_id="default", name="kbA")
    db.add(kb)
    db.commit()
    db.add_all([UserKbGrant(user_id=u.id, kb_id=kb.id),
                UserSession(token_hash="a" * 64, user_id=u.id,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=7))])
    db.commit()
    assert db.get(UserSession, "a" * 64).user_id == u.id
    assert [g.kb_id for g in db.query(UserKbGrant).filter_by(user_id=u.id)] == [kb.id]


def test_grant_unique_and_cascade(db: Session):
    u = _mk_user(db)
    kb = KnowledgeBase(tenant_id="default", name="kbB")
    db.add(kb)
    db.commit()
    db.add_all([UserKbGrant(user_id=u.id, kb_id=kb.id), UserKbGrant(user_id=u.id, kb_id=kb.id)])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_audit_log_row(db: Session):
    a = AuditLog(tenant_id="default", user_email="a@x.com", action="login_failed",
                 detail={"why": "bad"}, ip="127.0.0.1")
    db.add(a)
    db.commit()
    got = db.get(AuditLog, a.id)
    assert got.created_at is not None and got.user_email == "a@x.com"


def test_settings_admin_bootstrap_fields():
    from app.core.config import get_settings
    s = get_settings()
    assert hasattr(s, "admin_email") and hasattr(s, "admin_password")
```

- [ ] **Step 2: 跑红** — `pytest tests/test_rbac_models.py -v` Expected: ImportError（UserKbGrant 等不存在）。

- [ ] **Step 3: 实现模型**（`backend/app/models/__init__.py`）

`User` 类内 `hashed_password` 之后加两列（server_default 让既有行/裸 SQL 插入也有值）：

```python
    name = Column(String(128), nullable=False, default="", server_default="")
    status = Column(String(16), nullable=False, default="active", server_default="active")  # active/disabled
```

文件尾部（License 前或后均可，保持 §3.3 注释风格）追加：

```python
class UserSession(Base):
    """DB 会话表：cookie 存随机原文，库里只落 SHA-256——改密/禁用/登出即删行吊销。"""
    __tablename__ = "user_sessions"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class AuditLog(Base):
    """审计（§5 事件表）：只记认证+写操作；detail 永不落口令/密钥明文。"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    user_email = Column(String(255), index=True)  # 登录失败时可空（还没主体）
    action = Column(String(32), nullable=False, index=True)
    target_type = Column(String(32))
    target_id = Column(Integer)
    detail = J(nullable=False, default=dict)
    ip = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class UserKbGrant(Base):
    """库级授权（admin 隐式全库，不落行）。"""
    __tablename__ = "user_kb_grants"
    __table_args__ = (UniqueConstraint("user_id", "kb_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
```

`backend/app/core/config.py` 在 `admin_token` 上方加（admin_token 保留待任务 6 删除）：

```python
    # 初始管理员播种（users 空表时生效；生产部署后应登录改密）
    admin_email: str = "admin@umax.local"
    admin_password: str = "umax-admin-dev"
```

- [ ] **Step 4: 跑绿** — `pytest tests/test_rbac_models.py -v` Expected: 5 passed（`hash_password` 是任务 1 已产物，不会挡红；本任务首跑的红灯只应来自新表 ImportError）。
- [ ] **Step 5: 全量回归 + Commit** — `pytest` 全绿后 `git add backend/app/models/__init__.py backend/app/core/config.py backend/tests/test_rbac_models.py && git commit -m "feat(rbac-2/9): 数据模型——User+name/status、user_sessions/audit_logs/user_kb_grants 三表、admin 播种配置项"`

---

### Task 3: 审计写入口 + auth 依赖链 + 四个认证端点（login/logout/me/change-password）

**Files:**
- Create: `backend/app/services/audit.py`
- Modify: `backend/app/main.py`（create_app 内新增 auth 段；**本任务不动旧 require_admin/端点**，共存到任务 6）
- Modify: `backend/tests/conftest.py`（seed_user/login 助手）
- Test: `backend/tests/test_auth_api.py`

**Interfaces:**
- Consumes: 任务 1 `hash_password/verify_password/token_digest/new_session_token/LoginThrottle`；任务 2 表 + `Settings`。
- Produces:
  - `audit.py`: `ACTIONS: set[str]`、`record(session, action, *, user_email=None, target_type=None, target_id=None, detail=None, ip=None) -> AuditLog`（只 add 不 commit，调用方同事务提交）。
  - main.py create_app 内：常量 `SESSION_COOKIE="umax_session"`、`SESSION_TTL_DAYS=7`；依赖 `get_user(request, session) -> User`（401 "需要登录"；挂载 `request.state.user`/`request.state.session_hash`）、`require_admin_role(user) -> User`（403 "需要管理员权限"）、`allowed_kb_ids(session, user) -> set[int] | None`（None=admin 全库）；错误声明 `_ERR_UNAUTH=_ERR(401,"需要登录")`、`_ERR_FORBID=_ERR(403,"需要管理员权限")`。任务 4/5/6 直接复用这些名字，不得改名。
  - conftest：`seed_user(engine, email, password, *, role="member", name="") -> int`、`login(client, email, password) -> None`（断言 204）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_auth_api.py
# 认证端点全行为：登录/会话/me/登出/自助改密/吊销链/限流——账号体系的心脏，逐条对应 spec §2/§3.1
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.models import User, UserSession
from app.services.auth import hash_password
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


def _client(engine, tmp_path):
    app = create_app(engine=engine, secret="auth-secret", embedder=None,
                     chat_fn=None, upload_dir=str(tmp_path))
    return TestClient(app)


@pytest.fixture
def client(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    seed_user(engine, *ADMIN, role="admin", name="总管")
    seed_user(engine, *MEMBER, role="member", name="小王")
    return c


def test_login_sets_httponly_cookie_and_me_returns_profile(client):
    login(client, *ADMIN)
    me = client.get("/api/v1/auth/me").json()
    assert me == {"email": ADMIN[0], "name": "总管", "role": "admin", "kb_ids": None}
    assert any(c.name == "umax_session" for c in client.cookies)  # httpx 已托管会话 cookie
    r = client.post("/api/v1/auth/login", json={"email": MEMBER[0], "password": MEMBER[1]})
    assert r.status_code == 204
    sc = r.headers["set-cookie"]
    assert "umax_session=" in sc and "httponly" in sc.lower() and "samesite=lax" in sc.lower()
    assert client.get("/api/v1/auth/me").json()["role"] == "member"  # 新 cookie 覆盖生效


def test_member_me_kb_ids_is_sorted_grants(client, engine, db):
    from app.models import KnowledgeBase, UserKbGrant
    from sqlalchemy.orm import Session
    kb_a = KnowledgeBase(tenant_id="default", name="A")
    kb_b = KnowledgeBase(tenant_id="default", name="B")
    with Session(engine) as s:
        s.add_all([kb_a, kb_b])
        s.commit()
        uid = s.query(User).filter_by(email=MEMBER[0]).first().id
        s.add_all([UserKbGrant(user_id=uid, kb_id=kb_b.id), UserKbGrant(user_id=uid, kb_id=kb_a.id)])
        s.commit()
    login(client, *MEMBER)
    me = client.get("/api/v1/auth/me").json()
    assert me["kb_ids"] == sorted([kb_a.id, kb_b.id])


@pytest.mark.parametrize("email,password", [(ADMIN[0], "nope"), ("ghost@x.com", "whatever")])
def test_login_401_uniform_no_enumeration(client, email, password):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401 and r.json()["detail"] == "邮箱或口令错误"
    assert client.get("/api/v1/auth/me").status_code == 401  # 失败不发会话


def test_throttle_429_after_10_failures(client):
    for _ in range(10):
        assert client.post("/api/v1/auth/login",
                           json={"email": ADMIN[0], "password": "bad"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": "bad"})
    assert r.status_code == 429
    r2 = client.post("/api/v1/auth/login", json={"email": MEMBER[0], "password": MEMBER[1]})
    assert r2.status_code == 204, "限流键含邮箱，不牵连他人"


def test_unauthenticated_me_and_logout_are_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_logout_clears_server_session(client, engine):
    login(client, *ADMIN)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        assert s.query(UserSession).count() == 0, "吊销=删行，cookie 残值作废"


def test_expired_session_rejected(client, engine):
    login(client, *ADMIN)
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        row = s.query(UserSession).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_disabled_user_existing_session_dies(client, engine, db):
    from sqlalchemy.orm import Session
    login(client, *MEMBER)
    with Session(engine) as s:
        s.query(User).filter_by(email=MEMBER[0]).first().status = "disabled"
        s.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_change_password_verifies_old_and_kicks_other_sessions(client, engine):
    from sqlalchemy.orm import Session
    login(client, *MEMBER)
    other = TestClient(client.app)                    # 同账号第二设备也在线
    login(other, *MEMBER)
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": "wrong-old", "new_password": "New-Pass-9"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": MEMBER[1], "new_password": "New-Pass-9"})
    assert r.status_code == 204
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == MEMBER[0], "当前会话改密后仍活"
    assert other.get("/api/v1/auth/me").status_code == 401, "他人会话即刻作废"
    with Session(engine) as s:
        assert s.query(UserSession).count() == 1, "旧会话全部吊销，只留当前"
    # 旧口令已失效：401；新口令可重新登录
    anon = TestClient(client.app)
    assert anon.post("/api/v1/auth/login", json={"email": MEMBER[0], "password": MEMBER[1]}).status_code == 401
    login(anon, MEMBER[0], "New-Pass-9")


def test_change_password_rejects_short_new(client):
    login(client, *ADMIN)
    r = client.post("/api/v1/auth/change-password",
                    json={"old_password": ADMIN[1], "new_password": "short"})
    assert r.status_code == 422


def test_login_audit_events(client, engine, db):
    from app.models import AuditLog
    from sqlalchemy.orm import Session
    login(client, *ADMIN)
    client.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": "bad"})
    client.post("/api/v1/auth/logout")
    with Session(engine) as s:
        rows = s.query(AuditLog).order_by(AuditLog.id).all()
    assert [a.action for a in rows] == ["login_success", "login_failed", "logout"]
    assert all(a.ip for a in rows), "认证事件必带来源 ip"
```

conftest 追加（`backend/tests/conftest.py` 尾部）：

```python
# ---- RBAC 测试助手（任务 3 起共享）----
def seed_user(engine, email: str, password: str, *, role: str = "member", name: str = "") -> int:
    """直写 users 表（绕过端点，端点行为另有测试）。返回 user id。"""
    from app.models import User
    from app.services.auth import hash_password
    with Session(engine) as s:
        u = User(tenant_id="default", email=email, name=name or email.split("@")[0],
                 hashed_password=hash_password(password), role=role, status="active")
        s.add(u)
        s.commit()
        return u.id


def login(client, email: str, password: str) -> None:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 204, f"登录失败：{r.status_code} {r.text}"
```

- [ ] **Step 2: 跑红** — Expected: 404（auth 端点不存在）/record import 错。

- [ ] **Step 3: 实现 audit 写入口**（新文件）

```python
# backend/app/services/audit.py
# 审计统一写入口：只 add 行不 commit——与各写端点同事务，保证"操作成功才有审计"
from app.models import AuditLog

ACTIONS = {
    "login_success", "login_failed", "logout",
    "user_created", "user_updated", "grants_updated",
    "kb_created", "kb_deleted", "document_uploaded", "document_deleted",
    "document_reprocessed", "model_created", "model_updated", "model_deleted",
}


def record(session, action: str, *, user_email: str | None = None,
           target_type: str | None = None, target_id: int | None = None,
           detail: dict | None = None, ip: str | None = None) -> AuditLog:
    assert action in ACTIONS, f"未登记的审计动作：{action}（加动作必须先进 spec §5 事件表）"
    row = AuditLog(tenant_id="default", user_email=user_email, action=action,
                   target_type=target_type, target_id=target_id,
                   detail=detail or {}, ip=ip)
    session.add(row)
    return row
```

- [ ] **Step 4: 实现 auth 段**（`backend/app/main.py`，create_app 内、旧 require_admin 段之后；imports 补 `timedelta/timezone`、任务 1/2/auth/audit 符号）

```python
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
```

输入模型（main.py 顶层类区，仿 LoginIn 旧位）：

```python
class LoginIn(BaseModel):          # 既有 token 版（main.py:144）本任务改名 LegacyTokenLoginIn（含 /admin/login
    email: Utf8Str = Field(max_length=255)   # 端点的引用处，纯改名），新 LoginIn 占正式名；任务 6 连旧端点一并删除
    password: Utf8Str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    old_password: Utf8Str = Field(max_length=256)
    new_password: Utf8Str = Field(min_length=8, max_length=256)
```

端点四枚：

```python
    @app.post("/api/v1/auth/login", status_code=204,
              responses={**_ERR(401, "邮箱或口令错误"),
                         **_ERR(429, "失败次数过多，15 分钟后再试"), **_ERR_BODY})
    def auth_login(body: LoginIn, request: Request, response: Response,
                   session: Session = Depends(get_session)):
        key = f"{body.email}|{_client_ip(request)}"
        if throttle.blocked(key):
            raise HTTPException(429, "失败次数过多，请 15 分钟后再试")
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
              responses={**_ERR(401, "旧口令错误"), **_ERR(422, "新口令至少 8 位"), **_ERR_UNAUTH, **_ERR_BODY})
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
```

- [ ] **Step 5: 跑绿** — `pytest tests/test_auth_api.py -v` Expected: 全 passed；`pytest` 全量不回归。
- [ ] **Step 6: 契约同步（四步③④）** — `cd backend && ../.venv/Scripts/python.exe scripts/export_openapi.py`；`cd sdk-ts && npm test`（gen→新鲜度闸→tsc）。
- [ ] **Step 7: Commit** — `git add -A backend/app backend/tests contracts sdk-ts/src && git commit -m "feat(rbac-3/9): 认证端点四件套+DB 会话吊销+登录限流+审计写入口（登录/登出/改密/事件全测）"`

---

### Task 4: 用户管理 + 授权端点（/users、/users/{id}/grants）

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_users_api.py`

**Interfaces:**
- Consumes: 任务 3 `get_user/require_admin_role/audit_record/SESSION*` + conftest `seed_user/login`。
- Produces: `ROLES={"admin","member"}`、`USER_STATUSES={"active","disabled"}`、`_user_json(u, grants)` 形态 `{id,email,name,role,status,created_at:iso,kb_ids:number[]|null}`、`_grants_map(session)->dict[int,list[int]]`、`_revoke_sessions(session, user_id)`——任务 5 审计、任务 6 收紧、任务 8 前端消费。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_users_api.py
# 用户/授权端点：admin 独占、自身保护、吊销链、审计事件——逐条对应 spec §3.1
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


@pytest.fixture
def client(engine, db, tmp_path):
    c = TestClient(create_app(engine=engine, secret="u-secret", upload_dir=str(tmp_path)))
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER)
    login(c, *ADMIN)
    return c


def test_member_forbidden_and_anon_401_on_users_surface(client, engine, tmp_path):
    anon = TestClient(client.app)
    assert anon.get("/api/v1/users").status_code == 401
    login(anon, *MEMBER)
    for method, url in [("get", "/api/v1/users"), ("post", "/api/v1/users")]:
        r = getattr(anon, method)(url, json={"email": "x@y.com", "name": "x",
                                             "password": "Passw0rd-1", "role": "member"}
                                  if method == "post" else None)
        assert r.status_code == 403 and r.json()["detail"] == "需要管理员权限"


def test_create_list_update_flow(client, engine):
    r = client.post("/api/v1/users", json={"email": "new@x.com", "name": "新人",
                                           "password": "New-Pass-1", "role": "member"})
    assert r.status_code == 201
    u = r.json()
    assert u["kb_ids"] == [] and u["status"] == "active" and "hashed_password" not in u
    assert u["created_at"][:4] == "20"  # ISO 时间串（spec §3.1 列表列）
    rows = client.get("/api/v1/users").json()
    assert {x["email"] for x in rows} == {"admin@umax.local", "dev@umax.local", "new@x.com"}
    r = client.patch(f"/api/v1/users/{u['id']}", json={"name": "改名", "role": "admin"})
    assert r.json()["name"] == "改名" and r.json()["role"] == "admin"


def test_duplicate_email_400_and_bad_role_400(client):
    assert client.post("/api/v1/users", json={"email": "dev@umax.local", "name": "d",
                                              "password": "Passw0rd-1", "role": "member"}).status_code == 400
    assert client.post("/api/v1/users", json={"email": "ok@x.com", "name": "n",
                                              "password": "Passw0rd-1", "role": "root"}).status_code == 400


def test_self_demote_and_self_disable_are_400(client, engine):
    me = client.get("/api/v1/auth/me").json()
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == me["email"]][0]
    assert client.patch(f"/api/v1/users/{uid}", json={"role": "member"}).status_code == 400
    assert client.patch(f"/api/v1/users/{uid}", json={"status": "disabled"}).status_code == 400


def test_password_reset_kicks_target_sessions_but_not_operator(client, engine):
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == MEMBER[0]][0]
    target = TestClient(client.app)
    login(target, *MEMBER)
    assert client.patch(f"/api/v1/users/{uid}", json={"password": "Reset-Pass-9"}).status_code == 200
    assert target.get("/api/v1/auth/me").status_code == 401, "被重置者即刻踢出"
    assert client.get("/api/v1/auth/me").status_code == 200, "操作者会话不受影响"
    login(target, MEMBER[0], "Reset-Pass-9")          # 新口令可重新登录


def test_disable_kicks_sessions(client, engine):
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == MEMBER[0]][0]
    target = TestClient(client.app)
    login(target, *MEMBER)
    assert client.patch(f"/api/v1/users/{uid}", json={"status": "disabled"}).status_code == 200
    assert target.get("/api/v1/auth/me").status_code == 401
    assert target.post("/api/v1/auth/login",
                       json={"email": MEMBER[0], "password": MEMBER[1]}).status_code == 401, "禁用账号登不进"


def test_grants_put_get_and_admin_refused(client, engine, db):
    from app.models import KnowledgeBase
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        a, b = KnowledgeBase(tenant_id="default", name="A"), KnowledgeBase(tenant_id="default", name="B")
        s.add_all([a, b])
        s.commit()
        ids = [a.id, b.id]
    uid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == MEMBER[0]][0]
    aid = [u["id"] for u in client.get("/api/v1/users").json() if u["email"] == ADMIN[0]][0]
    assert client.get(f"/api/v1/users/{aid}/grants").status_code == 400
    assert client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": [ids[1], ids[0]]}).status_code == 200
    assert client.get(f"/api/v1/users/{uid}/grants").json() == sorted(ids)
    assert client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": [999999]}).status_code == 400
    assert client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": [ids[0]]}).status_code == 200
    assert client.get(f"/api/v1/users/{uid}/grants").json() == [ids[0]], "PUT=整集合替换"


def test_users_audit_events(client, engine):
    from app.models import AuditLog
    from sqlalchemy.orm import Session
    r = client.post("/api/v1/users", json={"email": "aud@x.com", "name": "a",
                                           "password": "Passw0rd-1", "role": "member"})
    uid = r.json()["id"]
    client.put(f"/api/v1/users/{uid}/grants", json={"kb_ids": []})
    client.patch(f"/api/v1/users/{uid}", json={"status": "disabled"})
    with Session(engine) as s:
        rows = [(a.action, a.target_id, a.detail) for a in s.query(AuditLog).filter(
            AuditLog.action.in_(["user_created", "grants_updated", "user_updated"]))]
    assert (rows[0][0], rows[0][1]) == ("user_created", uid)
    assert ("grants_updated", uid) in [(x[0], x[1]) for x in rows]
    assert any(x[0] == "user_updated" and x[2] == {"fields": ["status"]} for x in rows)
```

- [ ] **Step 2: 跑红** — 404/405。
- [ ] **Step 3: 实现**

输入模型（main.py 类区）：

```python
class UserIn(BaseModel):
    email: Utf8Str = Field(max_length=255)
    name: Utf8Str = Field(default="", max_length=128)
    password: Utf8Str = Field(min_length=8, max_length=256)
    role: str = Field(default="member", json_schema_extra={"pattern": ROLE_PATTERN})


class UserPatchIn(BaseModel):
    name: Utf8Str | None = Field(None, max_length=128)
    role: str | None = Field(None, json_schema_extra={"pattern": ROLE_PATTERN})
    status: str | None = Field(None, json_schema_extra={"pattern": USER_STATUS_PATTERN})
    password: Utf8Str | None = Field(min_length=8, max_length=256)


class GrantsIn(BaseModel):
    kb_ids: list[ReqId] = []
```

顶层常量（`SCENARIO_PATTERN` 同款派生法，防手改漂移）：

```python
ROLES = {"admin", "member"}
USER_STATUSES = {"active", "disabled"}
ROLE_PATTERN = "^(" + "|".join(sorted(ROLES)) + ")$"
USER_STATUS_PATTERN = "^(" + "|".join(sorted(USER_STATUSES)) + ")$"
```

端点（main.py auth 段之后；全部 `admin: User = Depends(require_admin_role)` + `request: Request`）：

```python
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
            _revoke_sessions(session, u.id)   # 角色/启停/重置口令变更一律全吊销（spec §2）
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
```

- [ ] **Step 4: 跑绿** — `pytest tests/test_users_api.py -v` + 全量。
- [ ] **Step 5: 契约③④ + Commit** — 重导 spec、`sdk-ts npm test`，commit `"feat(rbac-4/9): /users 与 /users/{id}/grants 管理端点（自身保护/吊销链/审计事件）"`

---

### Task 5: 审计查询端点 + 全写路径埋点

**Files:**
- Modify: `backend/app/main.py`（kb/文档/模型写端点 + 用量端点侧无审计；各写处 `audit_record`）
- Test: `backend/tests/test_audit_api.py`

**Interfaces:**
- Consumes: `audit_record/ACTIONS`、`require_admin_role`、既有写端点。
- Produces: `GET /api/v1/audit` 响应形态 `{id,user_email,action,target_type,target_id,detail,ip,created_at:iso}`；写端点新增 `request: User` 依赖形态（任务 6 收紧时沿用其 request/user 签名）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_audit_api.py
# 审计闭环：每类写操作落对应事件 + 查询过滤/分页 + detail 无明文
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.conftest import login, seed_user
from tests.test_models_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


@pytest.fixture
def client(engine, db, tmp_path):
    c = TestClient(create_app(engine=engine, secret="audit-secret",
                              embedder=FakeEmbedder(get_settings().embedding_dim),
                              chat_fn=lambda q, h: {"answer": "a", "prompt_tokens": 1,
                                                    "completion_tokens": 1},
                              upload_dir=str(tmp_path)))
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    return c


def _actions(client):
    return [r["action"] for r in client.get("/api/v1/audit").json()]


def test_write_paths_emit_events(client, tmp_path):
    kb = client.post("/api/v1/kb", json={"name": "kbAudit"}).json()
    files = {"file": ("a.txt", "内容足够切成块".encode("utf-8") * 10, "text/plain")}
    doc = client.post(f"/api/v1/kb/{kb['id']}/documents", files=files).json()
    client.post(f"/api/v1/documents/{doc['id']}/reprocess")
    m = client.post("/api/v1/models", json={"scenario": "chat", "provider": "p",
                                            "base_url": "http://x/v1", "api_key": "sk-k",
                                            "model_name": "m"}).json()
    client.patch(f"/api/v1/models/{m['id']}", json={"enabled": False})
    client.delete(f"/api/v1/models/{m['id']}")
    assert set(["kb_created", "document_uploaded", "document_reprocessed",
                "model_created", "model_updated", "model_deleted"]) <= set(_actions(client))


def test_documents_delete_event(client, engine, db):
    # 删库/删文档端点阶段 1 不存在，本任务无事件可测——ACTIONS 已预留 kb_deleted/
    # document_deleted（spec §5），未来加端点时按 test_write_paths_events 同型补断言。
    from app.services.audit import ACTIONS
    assert {"kb_deleted", "document_deleted"} <= ACTIONS


def test_audit_query_filters_and_paging(client):
    login_anon = TestClient(client.app)
    login_anon.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": "bad"})
    r = client.get("/api/v1/audit", params={"action": "login_failed"})
    rows = r.json()
    assert rows and all(x["action"] == "login_failed" for x in rows)
    assert all(x["user_email"] == ADMIN[0] for x in rows)
    assert client.get("/api/v1/audit", params={"user": "ghost@x.com"}).json() == []
    assert len(client.get("/api/v1/audit", params={"limit": 1}).json()) == 1
    page1 = client.get("/api/v1/audit", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/audit", params={"limit": 2, "offset": 2}).json()
    assert page1 and {x["id"] for x in page1}.isdisjoint({x["id"] for x in page2})
    ids = [x["id"] for x in page1]
    assert ids == sorted(ids, reverse=True), "倒序返回（created_at,id 双键，测试内即 id 倒序）"


def test_audit_requires_admin(client):
    anon = TestClient(client.app)
    assert anon.get("/api/v1/audit").status_code == 401


def test_detail_never_contains_secrets(client):
    client.post("/api/v1/users", json={"email": "sec@x.com", "name": "s",
                                       "password": "Super-Secret-PW", "role": "member"})
    raw = client.get("/api/v1/audit", params={"action": "user_created"}).text
    assert "Super-Secret-PW" not in raw
```

- [ ] **Step 2: 跑红** — audit 查询 404 / 事件缺失。
- [ ] **Step 3: 实现**

`GET /api/v1/audit`（main.py，users 端点之后）：

```python
    @app.get("/api/v1/audit", responses={**_ERR_UNAUTH, **_ERR_FORBID})
    def list_audit(user: Annotated[str | None, Query(max_length=255)] = None,
                   action: Annotated[str | None, Query(max_length=32)] = None,
                   limit: JsonInt = 50, offset: JsonInt = 0,
                   admin: User = Depends(require_admin_role),
                   session: Session = Depends(get_session)):
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        q = session.query(AuditLog)
        if user:
            q = q.filter(AuditLog.user_email == user)
        if action:
            q = q.filter(AuditLog.action == action)
        rows = (q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset(offset).limit(limit).all())
        return [{"id": r.id, "user_email": r.user_email, "action": r.action,
                 "target_type": r.target_type, "target_id": r.target_id, "detail": r.detail,
                 "ip": r.ip, "created_at": r.created_at.isoformat()} for r in rows]
```

（`from fastapi import Query`、`AuditLog/UserKbGrant/User/UserSession` 补进 models import。）

既有写端点埋点（每处加 `request: Request` 形参与 `audit_record`，commit 前一行；target 语义如下）：

| 端点 | action | target_type/id | detail |
|---|---|---|---|
| `create_kb` | `kb_created` | `"kb"`/新 id | `{"name": body.name}` |
| `upload_document` | `document_uploaded` | `"document"`/新 id | `{"kb_id": kb_id, "name": name}` |
| `patch_document` | — 不记（状态机内部推进，非人工写操作） | | |
| `reprocess` | `document_reprocessed` | `"document"`/doc.id | `{"kb_id": doc.kb_id}` |
| `create_model` | `model_created` | `"model"`/m.id | `{"scenario","model_name","fallback_rank"}` 无 key |
| `patch_model` | `model_updated` | `"model"`/id | `{"fields": sorted(data.keys())}` |
| `delete_model` | `model_deleted` | `"model"`/id | `{"model_name": m.model_name}`（删前取） |

`user_email` 统一 `Depends(require_admin)`→此任务先以"该端点已登录操作者"为准：在 `audit_record(..., user_email=admin_or_user.email)`；实现方式=这些端点本任务加 `user: User = Depends(get_user)`（写端点是 admin 语义的用 `require_admin_role`），操作者邮箱入审计。**旧 token 版 require_admin 保持共存**，任务 6 再全局切换。`document_deleted`/`kb_deleted` 事件：阶段 1 没有删文档/删库端点——本任务不新增端点（YAGNI），ACTIONS 常量保留该两枚举备用，spec §5 与之对齐由 spec 修订记录覆盖（在 commit message 注明）。

- [ ] **Step 4: 跑绿** — `pytest tests/test_audit_api.py -v` + 全量。
- [ ] **Step 5: 契约③④ + Commit** — `git add -A backend contracts sdk-ts/src && git commit -m "feat(rbac-5/9): GET /audit 查询端点 + kb/文档/模型写路径审计埋点"`

---

### Task 6: 全局鉴权收口——检索层过滤、旧 ADMIN_TOKEN 退役、存量测试换轨

**Files:**
- Modify: `backend/app/main.py`（核心：所有存量端点挂依赖 + 删除旧 token 方案）
- Modify: `backend/app/services/retrieval.py`（`allowed_kb_ids` 钳制）
- Modify: `backend/app/services/gateway.py`（`make_chat_fn` 去记账、`logged` 约定删除）
- Modify: `backend/app/core/config.py`（删 `admin_token`）
- Modify: `backend/tests/`——`test_api.py`、`test_chat.py`、`test_models_api.py`、`test_contract_fixes.py`、`test_contract_declared.py`、`test_contract.py`（fuzz 换 admin 会话态，Step 6）、`test_smoke_api.py`、`test_gateway.py`
- Delete: `backend/tests/test_admin_auth.py`（价值并入 test_permissions + test_contract_declared，见 Step 5）
- Test: `backend/tests/test_permissions.py`（新增，主证据文件）

**Interfaces:**
- Consumes: 任务 3 `get_user/require_admin_role/allowed_kb_ids/_ERR_UNAUTH/_ERR_FORBID`；任务 4 `_revoke_sessions`；任务 5 `audit_record`。
- Produces:
  - 端点终态：`/api/v1/health`、`/api/v1/auth/login` 匿名可达；**其余全部要求登录**；admin 面 = kb 写/models/usage/users/audit。
  - `retrieve(..., allowed_kb_ids: set[int] | None = None)`：None=admin 不限；集合（含**空集**）=谓词钳制，空集直接 `return []`（绝不因 falsy 空列表漏过滤）。
  - chat 记账终态：`UsageRecord.user_email=登录者邮箱`、`Conversation.user_email=登录者邮箱`；`chat_fn` 返回值不再读 `logged` 键（网关与 fake 都不记，端点统一记一次）。
  - `create_app(..., admin_token: str = "")` 参数删除；`build_production_app` 播种初始管理员。

- [ ] **Step 1: 写主证据测试（先红）**

```python
# backend/tests/test_permissions.py
# 本期主证据：过滤在检索层（SQL 谓词），不在展示层——A 库内容对无授权 member 的
# 召回/citations/kb 列表/文档列表全不可见；越权 403、跨用户资源 404、空授权 MISS。
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.models import AuditLog, Conversation, KnowledgeBase, UserKbGrant, UsageRecord, User
from app.services.auth import hash_password
from sqlalchemy.orm import Session
from tests.test_models_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")


def _fake_chat(q, hits):
    return {"answer": f"依据{len(hits)}段可答[1]", "prompt_tokens": 5, "completion_tokens": 2}


@pytest.fixture
def world(engine, tmp_path):
    """A/B 两库各入一篇内容互斥的真实文档（FakeEmbedder 真走向量 SQL 路）。"""
    with Session(engine) as s:
        s.add_all([
            User(tenant_id="default", email=ADMIN[0], name="admin", role="admin", status="active",
                 hashed_password=hash_password(ADMIN[1])),
            User(tenant_id="default", email=MEMBER[0], name="dev", role="member", status="active",
                 hashed_password=hash_password(MEMBER[1])),
        ])
        a, b = KnowledgeBase(tenant_id="default", name="A库"), KnowledgeBase(tenant_id="default", name="B库")
        s.add_all([a, b])
        s.flush()
        ids = {"a": a.id, "b": b.id}
        s.commit()
    app = create_app(engine=engine, secret="perm-secret",
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     chat_fn=_fake_chat, upload_dir=str(tmp_path))
    admin = TestClient(app)
    from tests.conftest import login
    login(admin, *ADMIN)
    for kb_id, content in [(ids["a"], "阿尔法限量款的发货时效是七十二小时"),
                           (ids["b"], "贝塔系列的退换货规则是七天无理由")]:
        kb = admin.post(f"/api/v1/kb/{kb_id}/documents",
                        files={"file": (f"{content[:2]}.txt", content.encode() * 20, "text/plain")})
        assert kb.status_code == 201
    member = TestClient(app)
    login(member, *MEMBER)
    return {"admin": admin, "member": member, "ids": ids, "engine": engine, "app": app}


def _grant(world, kb_key):
    with Session(world["engine"]) as s:
        uid = s.query(User).filter_by(email=MEMBER[0]).first().id
        s.add(UserKbGrant(user_id=uid, kb_id=world["ids"][kb_key]))
        s.commit()


def test_anonymous_cannot_touch_any_business_route(world):
    anon = TestClient(world["app"])
    for method, url in [("get", "/api/v1/kb"), ("get", "/api/v1/conversations"),
                        ("post", "/api/v1/chat"), ("post", "/api/v1/retrieve"),
                        ("get", "/api/v1/usage/summary"), ("get", "/api/v1/models")]:
        assert getattr(anon, method)(url, json={} if method == "post" else None).status_code == 401
    assert anon.get("/api/v1/health").status_code == 200, "健康检查保持匿名"


def test_member_kb_list_and_documents_scoped(world):
    _grant(world, "b")
    kb_ids = [k["id"] for k in world["member"].get("/api/v1/kb").json()]
    assert kb_ids == [world["ids"]["b"]]
    assert world["member"].get(f"/api/v1/kb/{world['ids']['a']}/documents").status_code == 404
    assert world["admin"].get(f"/api/v1/kb/{world['ids']['a']}/documents").status_code == 200


def test_search_layer_filter_zero_alpha_leak_in_retrieval(world):
    _grant(world, "b")
    rb = world["member"].post("/api/v1/retrieve", json={"query": "贝塔 退换货 规则"})
    assert rb.json(), "授权库 BM25 必须召回（防空集假通过）"
    assert all(h["kb_id"] == world["ids"]["b"] for h in rb.json())
    ra = world["member"].post("/api/v1/retrieve", json={"query": "阿尔法 发货 时效"})
    assert all(h["kb_id"] != world["ids"]["a"] for h in ra.json()), "检索结果不得含未授权库的块"
    r2 = world["member"].post("/api/v1/chat", json={"question": "阿尔法限量款的发货时效是多久"})
    for c in r2.json()["citations"]:
        assert "阿尔法" not in c["doc_name"] and "阿尔法" not in c["excerpt"], "引用零 A 内容"
    # B 库提问必须正常命中（证明过滤不是"全都搜不到"式假通过）
    r3 = world["member"].post("/api/v1/chat", json={"question": "贝塔 退换货 规则"})
    assert r3.json()["citations"], "命中测试断言：授权库必须可问答"
    assert r3.json()["citations"][0]["doc_name"] == "贝塔.txt", "文件名由 fixture content[:2] 唯一确定"


def test_explicit_unauthorized_kb_ids_403(world):
    _grant(world, "b")
    for url, body in [("/api/v1/chat", {"question": "x"}),
                      ("/api/v1/retrieve", {"query": "x"})]:
        r = world["member"].post(url, json={**body, "kb_ids": [world["ids"]["a"]]})
        assert r.status_code == 403
        assert world["member"].post(url, json={**body, "kb_ids": [world["ids"]["b"]]}).status_code == 200


def test_empty_grants_chat_returns_miss_never_full_corpus(world):
    r = world["member"].post("/api/v1/chat", json={"question": "阿尔法的发货时效"})
    assert r.status_code == 200 and r.json()["citations"] == []
    assert "资料里没有" in r.json()["answer"]


def test_conversations_isolated_per_user(world):
    _grant(world, "b")
    conv = world["member"].post("/api/v1/chat", json={"question": "贝塔 规则"}).json()["conversation_id"]
    assert world["member"].get("/api/v1/conversations").json()[0]["id"] == conv
    assert world["admin"].get("/api/v1/conversations").json() == [], "admin 也没聊过——看不到别人的"
    assert world["admin"].get(f"/api/v1/conversations/{conv}/messages").status_code == 404
    assert world["member"].get(f"/api/v1/conversations/{conv}/messages").status_code == 200


def test_chat_records_real_user(world):
    _grant(world, "b")
    world["member"].post("/api/v1/chat", json={"question": "贝塔 规则"})
    with Session(world["engine"]) as s:
        conv = s.query(Conversation).first()
        usage = s.query(UsageRecord).order_by(UsageRecord.id.desc()).first()
    assert conv.user_email == MEMBER[0]
    assert usage.user_email == MEMBER[0], "端点必记且只记一次，台账归真实登录人"


def test_role_matrix_on_admin_surface(world):
    m = world["member"]
    assert m.post("/api/v1/kb", json={"name": "x"}).status_code == 403
    assert m.get("/api/v1/models").status_code == 403
    assert m.get("/api/v1/usage/summary").status_code == 403
    assert m.get("/api/v1/audit").status_code == 403
    assert m.get("/api/v1/users").status_code == 403
```

> 断言 `h["kb_id"]` 需要 retrieve 结果携带 kb_id——Step 3 检索实现里 `_load_chunks` SELECT 增 `c.kb_id` 并补进 hits dict（引用/citations 同源），此改动由本测试先红驱动。

- [ ] **Step 2: 跑红** — 匿名 200、越权成功等（预期大一片红）。

- [ ] **Step 3: 实现收口**

1) `retrieval.py`：`retrieve()` 签名加 `allowed_kb_ids: set[int] | None = None`；函数体开头：

```python
    if allowed_kb_ids is not None:
        scope = (set(kb_ids) & allowed_kb_ids) if kb_ids else set(allowed_kb_ids)
        if not scope:
            return []           # 空授权=空结果——绝不落到"无过滤全库"
        kb_ids = sorted(scope)  # 两路（_load_chunks/BM25 语料 与 _vector_ranking）天然都走 ANY(:kb_ids) 谓词
```

`_load_chunks` 的 SELECT 加 `c.kb_id`（hits 带出处库 id，越权断言与网关 kb_id 记账都吃它）。

2) `gateway.py`：记账职责收归端点，`logged` 约定整体退役。`ModelGateway.chat` 签名加 `log: bool = True`——`log=True` 时维持现状调 `_log`（直连路径归它记账），`log=False` 只返回结果不落账。`make_chat_fn` 内部改调 `self.chat(..., log=False)`，返回 dict 删除 `"logged": True` 键，`user_email` 参数从 make_chat_fn 签名中移除（不再需要——它不记账）。main.py 的 `chat_api` 相应收口：删掉 `if not out.get("logged")` 判断，端点拿到结果后**必记且只记一次** `UsageRecord(user_email=登录者邮箱)`；FakeChat/注入 chat_fn 少字段时 tokens 用 `out.get(...) or 0` 兜底，与现行为一致。

3) `main.py` 端点表切换（逐个挂依赖 + 收口规则）：

| 端点 | 新依赖 | 行为变化 |
|---|---|---|
| `health` | 无 | 不变 |
| `create_kb` | `admin=Depends(require_admin_role)` | responses 补 401/403 |
| `list_kb` | `user=Depends(get_user)` | `allowed=allowed_kb_ids(...)`；非 None 时 `.filter(KnowledgeBase.id.in_(allowed))` |
| `list_documents` | `user` | kb 不存在或不在 allowed → 404（同文案"知识库不存在"，防探测） |
| `upload_document` | `require_admin_role` | — |
| `get_document`/`preview_chunks` | `user` | `doc.kb_id` ∉ allowed → 404"文档不存在" |
| `patch_document`/`reprocess` | `require_admin_role` | — |
| `retrieve_api` | `user` | `retrieve(..., allowed_kb_ids=allowed)`；`body.kb_ids` 越权 → 403 |
| `chat` | `user` | 同上越权 403；`Conversation.user_email=user.email`；existing conv `conv.user_email != user.email → 404`；`UsageRecord.user_email=user.email`；不再读 `out.get("logged")`，恒记一次（chat_fn None 或 miss 路径不记） |
| `list_conversations` | `user` | `.filter(Conversation.user_email == user.email)` |
| `list_messages` | `user` | conv 非本人 → 404（同文案） |
| models×4 / usage | `require_admin_role` | responses 401/403 |

chat 记录块终型：

```python
        allowed = allowed_kb_ids(session, user)
        if body.kb_ids and not set(body.kb_ids) <= (allowed if allowed is not None else set(body.kb_ids)):
            raise HTTPException(403, "无权访问指定知识库")
        hits = retrieve(session, body.question, embedder=embedder, kb_ids=body.kb_ids,
                        allowed_kb_ids=allowed, recall_k=s.recall_k, top_k=s.rerank_top_n,
                        min_sim=s.min_sim)
        conv = session.get(Conversation, body.conversation_id) if body.conversation_id else None
        if conv is not None and conv.user_email != user.email:
            raise HTTPException(404, "会话不存在")
        if conv is None:
            conv = Conversation(tenant_id="default", user_email=user.email, ...)
        ...
        if hits and chat_fn is not None:
            out = chat_fn(body.question, hits)
            session.add(UsageRecord(tenant_id="default", user_email=user.email,
                                    scenario="chat", model=out.get("model") or s.chat_model,
                                    prompt_tokens=..., completion_tokens=..., latency_ms=out.get("latency_ms")))
```

（403 判定：`allowed is not None and not set(body.kb_ids or []) <= allowed`。）

4) 旧方案删除：`LoginIn`(token)、`admin_login/admin_logout` 端点、token 版 `require_admin`、`admin_session/admin_hint` 全部字样；`create_app` 参数去 `admin_token`；`config.py` 去 `admin_token`。`build_production_app` 播种+警告改型：

```python
    with Session(engine) as s:
        if s.query(User).first() is None:
            s.add(User(tenant_id="default", email=s2.admin_email, name="admin",
                       hashed_password=hash_password(s2.admin_password), role="admin"))
            s.commit()
            import logging
            logging.getLogger("umax").warning(
                "已播种初始管理员 %s（ADMIN_EMAIL/ADMIN_PASSWORD）——部署后立即登录改密", s2.admin_email)
```

users 表既有列补齐（create_all 不加列；老库一键启动的 pragmatics，放 `build_production_app`，紧邻 create_all 之后）：

```python
        with engine.begin() as con:   # 无 Alembic 过渡：幂等 ADD COLUMN IF NOT EXISTS（fresh 库同样通过）
            con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR(128) NOT NULL DEFAULT ''"))
            con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'"))
```

5) 存量测试换轨（模式统一）：各文件 `client` 夹具加 `seed_user(engine, ADMIN_EMAIL, PW, role="admin") + login(c, ...)`。`test_admin_auth.py` 整文件删除——它的两块价值已被吸收：admin 面声明清单进 `test_contract_declared.py`（Step 6 双轨守护），member 403/匿名 401 矩阵进 `test_permissions.py`，不再单设口令版守卫文件。`test_contract_fixes.py`：匿名请求先撞 401 再谈 422（FastAPI 先解依赖后校 body——依赖抛 401/403 优先于参数校验 422），该文件中依赖匿名 client 的 422/400 断言改为登录 admin 后发出；已登录态的格式类 422 断言不动。`test_gateway.py`：`make_chat_fn` 记账删除的相关断言改为 `chat(log=False)` 不记账 / `chat(log=True)` 记账两轨。`test_chat.py`：会话/台账邮箱断言改登录者。`test_smoke_api.py`：`build_production_app` 在 users 空表时播种 admin（Step 3 第 4 条），db 夹具清表后每次装配都会重播——冒烟脚本在建库前先 `client.post("/api/v1/auth/login", json={"email": s.admin_email, "password": s.admin_password})` 拿会话。

6) `test_contract_declared.py` 重写：

```python
EXPECTED = {
    # 全受护端点声明 401；admin 面加 403；逐端点业务码保留
    ("/api/v1/kb", "post"): {"401", "403"},
    ("/api/v1/kb", "get"): {"401"},
    ("/api/v1/kb/{kb_id}/documents", "get"): {"401", "404"},
    ("/api/v1/kb/{kb_id}/documents", "post"): {"401", "403", "404", "415"},
    ("/api/v1/documents/{doc_id}", "get"): {"401", "404"},
    ("/api/v1/documents/{doc_id}", "patch"): {"401", "403", "404"},
    ("/api/v1/documents/{doc_id}/reprocess", "post"): {"401", "403", "404"},
    ("/api/v1/documents/{doc_id}/chunks", "get"): {"401", "404"},
    ("/api/v1/retrieve", "post"): {"401", "403"},
    ("/api/v1/chat", "post"): {"401", "403"},
    ("/api/v1/conversations", "get"): {"401"},
    ("/api/v1/conversations/{conv_id}/messages", "get"): {"401", "404"},
    ("/api/v1/models", "get"): {"401", "403"},
    ("/api/v1/models", "post"): {"400", "401", "403", "503"},
    ("/api/v1/models/{model_id}", "patch"): {"400", "401", "403", "404", "503"},
    ("/api/v1/models/{model_id}", "delete"): {"401", "403", "503"},
    ("/api/v1/usage/summary", "get"): {"401", "403"},
    ("/api/v1/auth/login", "post"): {"401", "429"},
    ("/api/v1/auth/logout", "post"): {"401"},
    ("/api/v1/auth/me", "get"): {"401"},
    ("/api/v1/auth/change-password", "post"): {"401"},
    ("/api/v1/users", "get"): {"401", "403"},
    ("/api/v1/users", "post"): {"400", "401", "403"},
    ("/api/v1/users/{user_id}", "patch"): {"400", "401", "403", "404"},
    ("/api/v1/users/{user_id}/grants", "get"): {"400", "401", "403", "404"},
    ("/api/v1/users/{user_id}/grants", "put"): {"400", "401", "403", "404"},
    ("/api/v1/audit", "get"): {"401", "403"},
}
```

守护二改双轨：`login_surface` = 声明 401 的端点全集 == `set(EXPECTED) - {("/api/v1/health","get"), ("/api/v1/auth/login","post")}`；`admin_surface` = 声明 403 的端点全集 == `{p for p,c in EXPECTED if "403" in c}`。`/health` 保持零声明。

7) `test_contract.py` 换轨（否则 fuzz 全撞 401 墙，退化成 401 压力测试）：夹具内先真登录一个 admin，再用薄 ASGI 包装器给 fuzz 请求注入会话 cookie——401/403 矩阵由 test_permissions + declared 双守护专门负责，fuzz 回归其"业务逻辑符合 schema"本职：

```python
class _InjectSession:
    """给所有 fuzz 请求带 admin 会话 cookie：spec 未声明 securityScheme，只能注入头。
    login 端点本身也被 fuzz（带 cookie 调它无碍：成功=重定向新会话，失败在限流键 email|ip 上自隔离）。"""
    def __init__(self, app, cookie: str):
        self.app, self.raw = app, f"umax_session={cookie}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not any(k == b"cookie" for k, _ in scope["headers"]):
            scope = {**scope, "headers": [*scope["headers"], (b"cookie", self.raw)]}
        await self.app(scope, receive, send)


@pytest.fixture
def contract_schema(engine, db, tmp_path):
    """夹具内建 ASGI 应用（登录态）并由 openapi.from_asgi 内省 spec（零外呼、不起服务）。"""
    def fake_chat(query, hits):
        return {"answer": "fuzz[1]", "prompt_tokens": 1, "completion_tokens": 1}
    app = create_app(engine=engine, secret="contract-secret",
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     chat_fn=fake_chat, upload_dir=str(tmp_path))
    seed_user(engine, "contract-admin@x.com", "C-Pass-1234", role="admin")
    probe = TestClient(app)
    probe.post("/api/v1/auth/login",
               json={"email": "contract-admin@x.com", "password": "C-Pass-1234"})
    token = probe.cookies.get("umax_session")
    assert token, "登录夹具自证：拿得到会话 cookie 再谈注入"
    return schemathesis.openapi.from_asgi("/openapi.json", _InjectSession(app, token), config=_cfg)
```

（`from_fixture("contract_schema")` 参数化、`_cfg` expected_statuses 均维持现状不动；`TestClient` 从 `fastapi.testclient` 导入，seed_user/login 从 conftest 导入。）

- [ ] **Step 4: 跑绿（全量）** — `pytest -v`：Expected: 全部通过（含新 403/401 矩阵与检索过滤主证据）。fuzz（`pytest -m contract`）确认 429/401/403/404 声明面自洽。
- [ ] **Step 5: 契约③④** — 重导 spec（admin 旧端点从 paths 消失）+ `sdk-ts npm test`。
- [ ] **Step 6: Commit** — `git commit -m "feat(rbac-6/9): 全局鉴权收口——全员登录/角色 admin 面/检索层 kb 谓词过滤/chat 归真实用户；ADMIN_TOKEN 方案退役+播种；契约守护换轨"`

---

### Task 7: 前端认证基座——AuthProvider + 登录/门闸/Nav/聊天页换轨

**Files:**
- Modify: `frontend/src/lib/paths.ts`、`frontend/src/lib/api.ts`、`frontend/src/lib/types.ts`
- Create: `frontend/src/lib/auth.tsx`
- Modify: `frontend/src/components/LoginCard.tsx`、`AdminGate.tsx`、`Nav.tsx`、`ChatApp.tsx`
- Modify: `frontend/src/app/admin/login/page.tsx`、`frontend/src/app/layout.tsx`（挂 Provider）
- Test: `frontend/src/lib/__tests__/auth.test.tsx`（新）、`frontend/src/components/__tests__/*.test.tsx`（改）

**Interfaces:**
- Consumes: sdk-ts 再生成的 auth 路径类型（Task 6 产物）。
- Produces: `AuthContext`：`{me: AuthMe | null, loaded: boolean, refresh(): Promise<void>, login(email, pw): Promise<AuthMe>, logout(): Promise<void>}`；`useAuth()`；`api.ts` 新增 `breathe`→`redirectLogin()` 注入点（`setUnauthorizedHandler`，默认 no-op，layout 注 `() => location.assign("/admin/login")`）；`types.ts` 新增 `AuthMe {email,name,role:"admin"|"member",kb_ids:number[]|null}`、`UserOut`、`AuditOut`。

- [ ] **Step 1: 写失败测试**

```tsx
// src/lib/__tests__/auth.test.tsx
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/lib/auth";
import { fakeApi, ok, fail } from "@/lib/testkit";

const ME = { email: "a@x.com", name: "a", role: "admin" as const, kb_ids: null };

function Probe({ client }: { client: never }) {
  const { me, loaded, login } = useAuth();
  return (
    <div>
      <span data-testid="state">{loaded ? (me ? me.role : "anon") : "loading"}</span>
      <button onClick={() => void login("a@x.com", "pw")}>login</button>
    </div>
  );
}

it("挂载即拉 /auth/me：200→admin，401→anon", async () => {
  const api = fakeApi({ GET: (u) => (u.includes("/auth/me") ? ok(ME) : ok([])) }) as never;
  render(<AuthProvider client={api}><Probe client={api} /></AuthProvider>);
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("admin"));
});

it("me 401 → 匿名态不抛错；login 成功后重拉", async () => {
  let me = fail("需要登录", 401);
  const api = { GET: vi.fn((u: string) => (u.includes("/auth/me") ? me : ok([]))),
               POST: vi.fn(() => { me = ok(ME); return ok(undefined); }) } as never;
  render(<AuthProvider client={api}><Probe client={api} /></AuthProvider>);
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("anon"));
  await act(async () => { await userEvent.click(screen.getByText("login")); });
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("admin"));
});
```

（LoginCard/Nav/AdminGate 的组件级测试按各文件既有测试风格各补 2-3 例：角色分流跳转、member 访问 admin 被弹回 `/`、429 文案、未登录 Nav 显隐。）

- [ ] **Step 2: 跑红** — import 不存在。

- [ ] **Step 3: 实现**

`paths.ts`：删 `adminLogin/adminLogout`，加：

```ts
  authLogin: "/api/v1/auth/login",
  authLogout: "/api/v1/auth/logout",
  authMe: "/api/v1/auth/me",
  authChangePassword: "/api/v1/auth/change-password",
  users: "/api/v1/users",
  user: "/api/v1/users/{user_id}",
  userGrants: "/api/v1/users/{user_id}/grants",
  audit: "/api/v1/audit",
```

`api.ts`：删 `logout()/isAdminHint()`；`call/callVoid` 捕 401 时触发模块级钩子后再抛：

```ts
let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void): void { onUnauthorized = fn; }
// call()/callVoid() 内 error/无 data 分支：if (response?.status === 401) onUnauthorized(); 然后照旧 throw
```

`lib/auth.tsx`（客户端组件，仿 hooks.ts 的 useAsync 风格）：

```tsx
"use client";
// 全员登录基座：/auth/me 唯一真相源（admin_hint cookie 已退役）。挂载拉一次，登录/登出后 refresh。
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api as defaultApi, call, callVoid, is401, type Client } from "./api";
import { P } from "./paths";
import type { AuthMe } from "./types";

type AuthState = {
  me: AuthMe | null; loaded: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<AuthMe>;
  logout: () => Promise<void>;
};
const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children, client = defaultApi }:
    { children: ReactNode; client?: Client }) {
  const [me, setMe] = useState<AuthMe | null>(null);
  const [loaded, setLoaded] = useState(false);

  const fetchMe = useCallback(async (): Promise<AuthMe | null> => {
    try { return await call(client.GET(P.authMe)); }
    catch (e) { if (is401(e)) return null; throw e; }
  }, [client]);

  const refresh = useCallback(async () => {
    setMe(await fetchMe());
    setLoaded(true);
  }, [fetchMe]);

  useEffect(() => { void refresh(); }, [refresh]);

  const login = useCallback(async (email: string, password: string): Promise<AuthMe> => {
    await callVoid(client.POST(P.authLogin, { body: { email, password } }));
    const next = await fetchMe();
    if (!next) throw new Error("登录后 me 为空");  // 204 成功后必须可读
    setMe(next); setLoaded(true);
    return next;
  }, [client, fetchMe]);

  const logout = useCallback(async () => {
    await callVoid(client.POST(P.authLogout, {} as never));
    setMe(null);
  }, [client]);

  return <Ctx.Provider value={{ me, loaded, refresh, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth 必须在 AuthProvider 内");
  return v;
}
```

`AdminGate.tsx` 重写（member 弹 `/`、未登录弹登录页、loading 不闪）：

```tsx
"use client";
// admin 页门闸：/auth/me 为真相源——未登录弹回登录页，member 弹回聊天。
// 体验层非安全层；真授权由后端 401/403 把关（AdminBanner 兜底保留）。
import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function AdminGate({ children }: { children: ReactNode }) {
  const { me, loaded } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (!loaded) return;
    if (!me) router.replace("/admin/login");
    else if (me.role !== "admin") router.replace("/");
  }, [loaded, me, router]);
  return me && me.role === "admin" ? <>{children}</> : null;
}
```

`useAdminHint()` 删除；`Nav.tsx` 改 `useAuth()`：`links = me?.role === "admin" ? LINKS : LINKS.slice(0, 1)`；右侧 `me ? <span>{me.name}</span> + 退出按钮（logout→跳 `/`）+（me.role==="admin" 时"改密"入口，Task 9 的 ChangePasswordDialog）`: path!="/admin/login" && 登录链接`。`ChatApp.tsx`：初始数据加载包在 `me` 就绪后（`const { me, loaded } = useAuth();` 未 loaded 渲染骨架；loaded 无 me → `router.replace("/admin/login")`）。`layout.tsx`：`<AuthProvider>` 包 children，并 `setUnauthorizedHandler` 于 client 壳组件 effect。`/admin/login/page.tsx`：文案改"登录"（全员），提交成功后 `router.push(m.role === "admin" ? "/admin/kb" : "/")`。

- [ ] **Step 4: 跑绿** — `cd frontend && npm test && npm run typecheck`。
- [ ] **Step 5: Commit** — `git commit -m "feat(rbac-7/9): 前端认证基座——AuthProvider(/auth/me 真相源)/登录页/角色门闸/Nav/聊天页换轨"`

---

### Task 8: 前端管理页——/admin/users（授权弹窗）+ /admin/audit（过滤分页）

**Files:**
- Create: `frontend/src/components/UsersAdmin.tsx`、`frontend/src/components/AuditAdmin.tsx`、`frontend/src/components/ChangePasswordDialog.tsx`
- Create: `frontend/src/app/admin/users/page.tsx`、`frontend/src/app/admin/audit/page.tsx`
- Test: `frontend/src/components/__tests__/usersAdmin.test.tsx`、`auditAdmin.test.tsx`、`changePassword.test.tsx`

**Interfaces:**
- Consumes: `AuthProvider/useAuth`（Task 7）、P 路径、types `UserOut/AuditOut`。
- Produces: 无（终端 UI）。

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/__tests__/usersAdmin.test.tsx
// 列表/新建/授权/禁用/自身保护——全部经 fakeApi 打 P 路径常量，断言请求面而非内部状态
// （调用形态与既有 ModelsAdmin.test.tsx 一致：api.X(P.y, { params: { path }, body })）
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UsersAdmin from "@/components/UsersAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const ME = { email: "admin@x.com", name: "admin", role: "admin" as const, kb_ids: null };
const rows = [
  { id: 1, email: "admin@x.com", name: "admin", role: "admin", status: "active",
    created_at: "2026-09-23T08:00:00", kb_ids: null },
  { id: 2, email: "dev@x.com", name: "dev", role: "member", status: "active",
    created_at: "2026-09-23T09:00:00", kb_ids: [7] },
];

const renderAdmin = (api: ReturnType<typeof fakeApi>) =>
  render(<UsersAdmin api={api} me={ME} />);

it("新建用户：POST P.users 带表单字段", async () => {
  const POST = vi.fn(() => ok({ ...rows[0], id: 3 }));
  renderAdmin(fakeApi({ GET: (u) => u === P.kb ? ok([{ id: 7, name: "库A", description: null }]) : ok(rows), POST }));
  await screen.findByText("dev@x.com");
  await userEvent.type(screen.getByLabelText("邮箱"), "n@x.com");
  await userEvent.type(screen.getByLabelText("姓名"), "新人");
  await userEvent.type(screen.getByLabelText("初始口令"), "Passw0rd-1");
  await userEvent.click(screen.getByRole("button", { name: "新建用户" }));
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  expect(POST).toHaveBeenCalledWith(P.users, expect.objectContaining({
    body: expect.objectContaining({ email: "n@x.com", name: "新人", role: "member" }),
  }));
});

it("授权弹窗：勾选后 PUT grants 收到整集合", async () => {
  const PUT = vi.fn(() => ok([7, 8]));
  renderAdmin(fakeApi({
    GET: (u) => u === P.kb ? ok([{ id: 7, name: "库A", description: null }, { id: 8, name: "库B", description: null }]) : ok(rows),
    PUT,
  }));
  const row = await screen.findByRole("row", { name: /dev@x\.com/ });
  await userEvent.click(within(row).getByRole("button", { name: "授权" }));
  const dialog = await screen.findByRole("dialog", { name: /dev@x\.com/ });
  await userEvent.click(within(dialog).getByLabelText("库B"));
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
  await waitFor(() => expect(PUT).toHaveBeenCalledWith(P.userGrants, expect.objectContaining({
    params: { path: { user_id: 2 } }, body: { kb_ids: [7, 8] },
  })));
});

it("禁用走 PATCH status；当前登录者行无降级/禁用按钮", async () => {
  const PATCH = vi.fn(() => ok(rows[1]));
  renderAdmin(fakeApi({ GET: (u) => u === P.kb ? ok([]) : ok(rows), PATCH }));
  const adminRow = await screen.findByRole("row", { name: /admin@x\.com/ });
  expect(within(adminRow).queryByRole("button", { name: "禁用" })).toBeNull();
  expect(within(adminRow).queryByRole("button", { name: "降级" })).toBeNull();
  const devRow = within(await screen.findByRole("row", { name: /dev@x\.com/ }));
  await userEvent.click(devRow.getByRole("button", { name: "禁用" }));
  await waitFor(() => expect(PATCH).toHaveBeenCalledWith(P.user, expect.objectContaining({
    params: { path: { user_id: 2 } }, body: { status: "disabled" },
  })));
});
```

```tsx
// frontend/src/components/__tests__/auditAdmin.test.tsx —— 查询参数装配在 init.params.query（URL 不带串）
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuditAdmin from "@/components/AuditAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const auditRow = (id: number) => ({
  id, user_email: "dev@x.com", action: "login_failed", target_type: null,
  target_id: null, detail: {}, ip: "127.0.0.1", created_at: "2026-09-23T10:00:00" });

it("查询与翻页：query 参数逐次装配", async () => {
  const GET = vi.fn((_u: string, init?: { params?: { query?: { offset?: number } } }) =>
    ok([auditRow((init?.params?.query?.offset ?? 0) + 1)]));
  render(<AuditAdmin api={fakeApi({ GET })} />);
  await screen.findByRole("row", { name: /login_failed/ });
  await userEvent.selectOptions(screen.getByLabelText("动作"), "login_failed");
  await userEvent.click(screen.getByRole("button", { name: "查询" }));
  await waitFor(() => expect(GET).toHaveBeenCalledWith(P.audit, expect.objectContaining({
    params: { query: expect.objectContaining({ action: "login_failed", limit: 50, offset: 0 }) },
  })));
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(GET).toHaveBeenCalledWith(P.audit, expect.objectContaining({
    params: { query: expect.objectContaining({ offset: 50 }) },
  })));
});

it("空结果渲染空态不渲染表行", async () => {
  render(<AuditAdmin api={fakeApi({ GET: () => ok([]) })} />);
  await screen.findByText("没有匹配的审计记录");
  expect(screen.queryAllByRole("row", { name: /login_/ })).toHaveLength(0);
});
```

```tsx
// frontend/src/components/__tests__/changePassword.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";
import { P } from "@/lib/paths";
import { fail, fakeApi } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const fill = async (oldPw: string, newPw: string, confirm: string) => {
  await userEvent.type(screen.getByLabelText("旧口令"), oldPw);
  await userEvent.type(screen.getByLabelText("新口令"), newPw);
  await userEvent.type(screen.getByLabelText("确认新口令"), confirm);
  await userEvent.click(screen.getByRole("button", { name: "确认修改" }));
};

it("旧口令错：401 detail 上屏", async () => {
  const POST = vi.fn(() => fail("邮箱或口令错误", 401));
  render(<ChangePasswordDialog api={fakeApi({ POST })} onClose={vi.fn()} />);
  await fill("wrong", "New-Pass-9", "New-Pass-9");
  expect(await screen.findByText("邮箱或口令错误")).toBeInTheDocument();
  expect(POST).toHaveBeenCalledWith(P.authChangePassword, expect.objectContaining({
    body: { old_password: "wrong", new_password: "New-Pass-9" },
  }));
});

it("两次新口令不一致：前端拦截，不发请求", async () => {
  const POST = vi.fn();
  render(<ChangePasswordDialog api={fakeApi({ POST })} onClose={vi.fn()} />);
  await fill("Old-Pass-9", "New-Pass-9", "New-Pass-0");
  expect(await screen.findByText("两次输入的新口令不一致")).toBeInTheDocument();
  expect(POST).not.toHaveBeenCalled();
});
```

（成功路径的 logout→跳登录在 Task 9 真机冒烟剧本里覆盖——vitest 里 router.push 是 Next 门闸副作用，既有 admin 组件测试同型不做该断言。P 新键 `users/user/userGrants/audit/authChangePassword` 在 Task 7 的 paths.ts 已铺。）

- [ ] **Step 2: 跑红**；**Step 3: 实现**

模式全部沿用既有 admin 组件：`useAsync/usePolling`、`rowBusy` 禁用、`ErrorBanner`、卡片 `px-6 py-5`、表头 `text-h3 font-medium`、按钮 `text-body font-medium`、禁 styleGuard 违禁类。要点：
- UsersAdmin：列表 + 新建卡（email/name/password/role 下拉）+ 行操作（角色下拉 PATCH、禁用/启用 PATCH、重置口令内联双输入、授权弹窗：库 checkbox 取 `GET /api/v1/kb`，PUT grants）；当前登录者行（`me.email===row.email`）不渲染"降级/禁用"。
- AuditAdmin：`user`（Input 邮箱）+ `action`（Select，选项=spec §5 常量表前端镜像）+ 表格（时间本地化/用户/动作/对象/IP/详情）+ `上一页/下一页`（offset 步进 50，limit=50；`offset===0` 禁用上一页）。
- ChangePasswordDialog：Modal 三输入（旧/新/确认，前端断言两次一致）→ `POST /auth/change-password` → 成功后 `logout()` → 跳 `/admin/login`；Nav 在 me 存在时渲染触发按钮。
- 页面壳：`app/admin/users/page.tsx`、`app/admin/audit/page.tsx` = `<AdminGate><UsersAdmin/></AdminGate>` 同款。

- [ ] **Step 4: 跑绿** — `npm test` 全绿 + `npm run typecheck` + styleGuard 覆盖新文件（自动扫描）；Nav LINKS 追加 `{ href: "/admin/users", label: "用户" }`、`{ href: "/admin/audit", label: "审计" }`（admin 可见）。
- [ ] **Step 5: Commit** — `git commit -m "feat(rbac-8/9): /admin/users 账号与库级授权页 + /admin/audit 审计查询页 + 自助改密弹窗（UI 规范/styleGuard 通过）"`

---

### Task 9: 全量门禁 + 真机冒烟 + 文档收口

**Files:**
- Modify: `backend/.env.example`（ADMIN_TOKEN→ADMIN_EMAIL/ADMIN_PASSWORD）
- Modify: `README.md`、`产品需求与开发方案.md`（§6.5 追加第 10 条完成记录）
- Modify: 项目记忆 `project-stage1-status.md`（改名/新增阶段 2 状态条目）

- [ ] **Step 1: 全量门禁** — 停 frontend dev → `cd frontend && npm run build` → 复启 `npm run dev`；`cd backend && ../.venv/Scripts/python.exe -m pytest`（含 `-m contract`）；`cd sdk-ts && npm test`；`cd frontend && npm test && npm run typecheck`。Expected: 全绿。
- [ ] **Step 2: 真机冒烟（spec §7 剧本逐句执行）** — `backend/.env` 把 `ADMIN_TOKEN` 行替换为 `ADMIN_EMAIL=admin@umax.local`、`ADMIN_PASSWORD=<强口令>`（本地 .env，永不入库）；确认 users 表空后重启后端（播种+警告日志在案）；curl：匿名 `/chat` 401 → login 204+cookie → me。浏览器：admin 登录→建 member `dev@…`→建库 A/B→只授 B→member 登录（进聊天、库下拉只见 B）→"贝塔…规则"提问命中引用→A 库问题 MISS→Nav 无管理菜单→直访 `/admin/users` 弹回聊天；admin `/admin/audit` 过滤 `user=dev@…` 见 login_success 事件链→重置 member 口令→member 旧浏览器会话下一请求 401、新口令可登→改密弹窗自助换密。全程控制台零 error。
- [ ] **Step 3: 文档** — README"管理鉴权（轻量方案）"条目改为历史并新增阶段 2 完成条目（四端点+users/grants/audit+检索层过滤+播种，测试数字照实）；需求文档 §6.5 追加"10. ✅ 阶段 2·安全与身份（2026-09-23…）"；`.env.example` 更新（`ADMIN_TOKEN` 删，`ADMIN_EMAIL/ADMIN_PASSWORD` 加注释与生成建议）。
- [ ] **Step 4: Commit（等"提交"口令若已获；本任务代码即已获——文档同车）** — `git commit -m "docs(rbac-9/9): 阶段 2 安全与身份收口——README/需求文档 §6.5/env 模板 + 真机冒烟记录"`

---

## 依赖与顺序

`1→2→3→4→5→6`（后端串行，6 是收口闸）；`7` 依赖 6 的 spec/sdk；`8` 依赖 7；`9` 依赖全部。Task 6 完成前，旧 token 方案与新 auth 端点共存（`/admin/*` 仍可用），**部署切换只发生在 Task 9 重启之后**。
