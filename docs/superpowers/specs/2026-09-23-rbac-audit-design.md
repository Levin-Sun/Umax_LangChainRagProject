# 阶段 2 · 安全与身份（RBAC + 审计）设计文档

> 状态：经三轮分节评审（A 架构与数据模型 / B API 与权限强制 / C 前端与测试）逐节获批（2026-09-23）。
> 上游依据：《产品需求与开发方案.md》§2.2「用户与权限体系：可见性过滤做在检索层」、阶段 2 计划「RBAC 与部门级权限过滤（过滤在检索层）、审计日志」。
> 本期取代：阶段 1 的共享口令方案（`ADMIN_TOKEN` + `admin_session`/`admin_hint` cookie，2026-09-22 落地）。

## 0. 裁决记录（AskUserQuestion，2026-09-23）

| 裁决点 | 结论 |
|---|---|
| 本期主线 | 安全与身份（RBAC+审计），先于开放 API/白标/评测/传图 |
| 登录边界 | **全员登录**：聊天+管理都必须有账号，匿名问答退役 |
| 权限粒度 | **角色（admin/member）+ 库级授权**，过滤在检索/查询 SQL 层；不做部门主数据、不做行级 ACL |
| 审计范围 | 认证 + 全部写操作，含管理端查看页；问答内容由 conversations/usage_records 天然覆盖，不重复记 |
| 会话机制 | **DB 会话表** + 不透明随机 token（HttpOnly cookie），非 JWT、非签名 cookie；口令哈希用 stdlib `hashlib.scrypt`（零新依赖） |

## 1. 数据模型（`backend/app/models/`，沿用 create_all，无 Alembic）

### 1.1 新表

- **`sessions`**：`token_hash`(String PK，token 的 SHA-256 hex)、`user_id`(FK users, ondelete CASCADE)、`created_at`、`expires_at`(带时区，索引)。
  落库的是 token 哈希——DB 泄露也造不出可用 cookie；cookie 值是 32 字节随机原文（`secrets.token_bytes`）。有效期 7 天，不做滑动续期。
- **`audit_logs`**：`id`、`tenant_id`（沿用 T() 默认 "default"）、`user_email`(String，可空——登录失败时无主)、`action`(String，见 §5 事件表)、`target_type`(String 可空)、`target_id`(Integer 可空)、`detail`(JSONB，摘要，**永不落口令/密钥明文**)、`ip`(String 可空)、`created_at`(索引)；`(user_email)` 索引。
- **`user_kb_grants`**：`user_id`(FK users CASCADE)、`kb_id`(FK knowledge_bases CASCADE)，联合唯一。**admin 隐式全库，不落授权行。**

### 1.2 users 表扩展

新增 `name`(String(128)，显示名)、`status`(String(16)，`active`/`disabled`，默认 active)。`hashed_password` 改用 `hashlib.scrypt`（参数 n=2^14,r=8,p=1，16 字节盐，格式 `scrypt$n$r$p$salt_b64$hash_b64`，`secrets.compare_digest` 比对）。`role` 沿用 `admin`/`member`。

### 1.3 存量表语义收紧（列不动）

- `conversations.user_email` / `usage_records.user_email`：从匿名占位改为**真实登录邮箱**；会话历史按 `user_email = 当前用户` 天然隔离。
- `chunks.acl`：本期**不启用**（行级 ACL 已裁决不做），保持 NULL。

### 1.4 初始管理员播种与旧鉴权退役

启动时（`build_production_app`）若 `users` 表为空：按 env `ADMIN_EMAIL`/`ADMIN_PASSWORD` 播种 role=admin 用户并打 warning 日志；两者未配则用默认 `admin@umax.local` / `umax-admin-dev`（同样打 warning，提示尽快改密）。`config.py` 新增 `admin_email`/`admin_password` 两项，**删除 `admin_token`**；`admin_session`/`admin_hint` cookie 与其读写代码、`/admin/login`、`/admin/logout` 端点整体退役，不留兼容层（当前仅开发环境在跑，无迁移负担；`.env.example` 同步更新）。

## 2. 登录态判定链

依赖 `current_user`（FastAPI dependency，全端点统一挂载）：
cookie `umax_session` → 查 `sessions`（存在且未过期）→ 查 `user`（`status=active`）→ 挂 `request.state.user`；任一环节断 → **401**。
依赖 `require_admin`（角色版，替换旧同名口令版）：`current_user` 且 `role=admin`，否则 **403**（未登录仍 401）。
吊销路径：登出删本行；改密/被 admin 重置口令/被禁用 → 删该用户全部 `sessions` 行，即刻全站踢出。

## 3. API 面（全部进 `contracts/openapi.json`，走 README 四步契约工作流）

### 3.1 新增端点

| 端点 | 谁能用 | 行为与错误 |
|---|---|---|
| `POST /api/v1/auth/login` {email,password} | 匿名 | 204 + Set-Cookie(HttpOnly, SameSite=Lax, path=/, 7天)；口令错 401；同 email+IP 15 分钟失败 ≥10 次 → 429（进程内计数，单机够用）；成功/失败均落审计 |
| `POST /api/v1/auth/logout` | 已登录 | 删 session 行 + delete_cookie，204 |
| `GET /api/v1/auth/me` | 已登录 | `{email,name,role,kb_ids}`；admin 的 `kb_ids` 为 `null`（全库），member 为授权库 id 数组。前端 Nav 显隐/登录跳转/选库的唯一真相源（取代 `admin_hint`） |
| `POST /api/v1/auth/change-password` {old_password,new_password} | 已登录 | 验旧口令（错→401）；改后踢**本账号其他**会话，204 |
| `GET /api/v1/users` | admin | 列表（email/name/role/status/created_at，不含哈希） |
| `POST /api/v1/users` {email,name,password,role} | admin | 201；email 租户内重复 → 400 |
| `PATCH /api/v1/users/{id}` {name?,role?,status?,password?} | admin | 改名/角色/启停/直接重置口令；**对自身降级或禁用 → 400**；role/status 变化与 password 重置触发该用户会话全吊销 |
| `GET /api/v1/users/{id}/grants` | admin | member 的可访问库 id 数组（admin 请求 → 400，隐式全库无需授权） |
| `PUT /api/v1/users/{id}/grants` {kb_ids} | admin | 整集合替换（删旧插新），落审计 |
| `GET /api/v1/audit?user=&action=&limit=&offset=` | admin | 只读分页（默认 limit 50，按 created_at 倒序）；无写入面 |

### 3.2 存量端点收紧（统一规则，不逐个例外）

- 除 `GET /health` 与 `POST /auth/login` 外**全部要求登录**（含 `POST /chat`、`POST /retrieve`、`GET /kb`、会话全部端点）。
- **数据过滤在 SQL 谓词层**：`allowed_kb_ids(user)`（admin=全库；member=grants 集）注入检索内核，BM25 与向量两路各自带 `kb_id IN :allowed` 谓词——不是召回后应用层裁剪；`GET /kb`、文档列表、分块预览按同一集合过滤。
- `POST /chat` / `POST /retrieve` 请求体 `kb_ids` 越权 → **403 显式拒绝**（不静默过滤，供契约测试锁定）。
- 跨用户访问他人 conversation → **404**（与"不存在"同形，防资源探测）。
- 知识库写/文档写/模型/用量端点：从口令版 `require_admin` 切到角色版，语义不变。
- 错误码三档全声明进 spec responses：401 未认证 / 403 无权限 / 404 越权资源；`test_contract_declared`、漂移守护、schemathesis fuzz 同步覆盖新端点。

## 4. 组件与数据流

- `app/core/security.py`（新）：scrypt 哈希/比对、随机 token 生成与哈希。纯函数，单元测试直测。
- `app/deps.py`（新或并入现有 core）：`get_db` 同层提供 `current_user`/`require_admin`/`allowed_kb_ids`；登录限流计数器（进程内 dict，email+IP 键，15 分钟窗口）。
- `app/services/audit.py`（新）：`record(session, action, *, user_email, target_type, target_id, detail, ip)`——与各写端点同事务提交，保证"操作成功才有审计"。
- 检索内核（`app/rag/`）：retrieve/chat 入参增加 `allowed_kb_ids: set[int]`，两路检索 SQL 谓词化；越权校验发生在端点层（403），谓词过滤发生在内核层（纵深两层）。
- 装配：`create_app(...)` 增参替换 `admin_token`（播种逻辑进 `build_production_app`）；测试经 `create_app` 注入，不依赖 env。

## 5. 审计事件表

`login_success` / `login_failed`(detail 记 email+ip，不记口令) / `logout` / `user_created` / `user_updated`(含角色/状态/重置口令，detail 只记改了哪些字段名) / `grants_updated`(detail 记新库 id 集合) / `kb_created` / `kb_deleted` / `document_uploaded` / `document_deleted` / `document_reprocessed` / `model_created` / `model_updated` / `model_deleted`。

## 6. 前端（`frontend/`，遵循 UI 设计规范：五级字阶/ink token/styleGuard 守护）

- `/admin/login` 升级为 email+口令（全员登录页）：admin 登录成功进 `/admin/kb`，member 回 `/` 聊天页；401/429 文案由 LoginCard 渲染（沿用现"口令错误"模式）。
- Nav：登录态真相源改 `GET /auth/me`（挂载 + 登录/登出后拉取）；显示用户名；admin 见"知识库/模型/用量/用户/审计"管理菜单，member 只见聊天+退出。**member 无管理页**，未登录者直访 `/admin/*` 弹回登录页（现状保持）。
- 聊天页选库列表 = 过滤后的 `GET /kb`（member 只见授权库）；任意请求 401 → `lib/api.ts` 错误映射统一跳登录页；删除 `admin_hint` 显隐逻辑。
- 新页 `/admin/users`：列表（表头规范样式）+ 新建表单（email/名/角色/初始口令）+ 行内操作（改角色/启停/重置口令）+ 授权弹窗（库 checkbox → PUT grants）；对当前登录 admin 自身行禁用"降级/禁用"操作（与后端 400 双保险）。
- 新页 `/admin/audit`：用户/动作两个下拉过滤 + 分页表格（时间、动作、对象、IP 列，数字列 font-medium）。
- sdk-ts 重新生成后，全部新调用经 `@umax/sdk-ts` 类型化 client，禁裸 fetch（现状铁律）。

## 7. 测试计划（TDD 先红后绿；真 PG `umaxrag_test`，函数级 TRUNCATE 隔离）

- `test_security.py`：scrypt 往返/错口令/篡改哈希格式；token 哈希稳定性。
- `test_auth.py`：登录 204+Set-Cookie、401、429 限流触发与恢复、me 的 admin(null)/member(数组) 形态、change-password 验旧+踢其他会话、登出吊销、过期 session 拒绝、禁用即刻 401。
- `test_users.py`：建档/重复 email 400/改名改角色/禁用/重置口令吊销/对自身降级与禁用 400。
- `test_permissions.py`（**本期主证据**）：A/B 两库各入真实索引文档，仅授 B 的 member：① 提问后召回与 citations 零 A 内容（检索层过滤，非展示层）；② `GET /kb` 只见 B；③ chat/retrieve 带 A → 403；④ 访问 admin 会话 → 404；⑤ member 调 users/audit/models → 403。
- `test_audit.py`：§5 全事件落表与字段、`?user=&action=` 过滤、分页倒序、detail 无口令明文断言。
- 契约：`test_contract_drift`/`test_contract_declared`/schemathesis `test_contract.py` 覆盖全部新端点与三档错误码。
- `sdk-ts`：`npm test`（gen → 新鲜度闸 → tsc 0 错误）。
- 前端 vitest+RTL：登录流（成功分角色跳转/401/429 文案）、`/auth/me` 驱动的 Nav 显隐、users 页建账号与授权弹窗、audit 页过滤分页；`styleGuard` 对新页扫描通过。
- 真机冒烟剧本（浏览器+curl）：admin 登录 → 建 member → 授 B 库 → member 登录只见 B → 带引用问答 → admin 审计页见全链路事件 → admin 重置 member 口令 → member 旧会话下一请求 401。

## 8. 明确不做（YAGNI 清单）

自助注册 / 部门主数据与部门树 / `chunks.acl` 行级过滤 / JWT 与密钥轮换 / 多设备会话管理页 / 审计导出 / ADMIN_TOKEN 兼容层 / 配额（属开放 API 期）。

## 9. 全局约束（继承，每个任务都受约束）

- 全程 TDD 铁律（红→绿→重构）；外部远程 API 用 MockTransport，真调用只留 smoke。
- 契约四步工作流：先测试 → 改后端 → `export_openapi.py` → 双端验证；前端禁裸 fetch。
- 密钥安全底线不变：加密存储/打码不回传/操作留审计；**不得变更 `GATEWAY_SECRET`**（现网已有密文）。
- UI 规范守护：新页面过 `styleGuard.test.ts`（默认字号/任意字号/leading/700+ 字重禁用，字阶只能来自五级定义）。
- 错误响应全部先声明进 openapi responses 再实现，`_ERR()` 模式沿用。
