# Umax_LangChainRagProject

> 把公司散落的文档，变成一个"随问随答、句句有出处"的企业智囊；一套系统做成模板，卖给一家复制一家。

面向中小企业（10~500 人）的企业级 RAG 知识库。经营模式：个人 OPC + vibecoding 开发，主打**私有化交付**（数据不出客户门）+ 授权费/年维保，后期扩多租户 SaaS。

## 技术栈

- 编排：LangChain（一期）+ LangGraph（三期 Agentic RAG）
- 后端：Python + FastAPI + PostgreSQL(pgvector) + ARQ 异步任务
- 解析：MinerU（扫描件/表格/图表）
- 检索：BM25 + 向量混合检索 + RRF 融合 + bge-reranker 重排
- 前端：Next.js + shadcn/ui（聊天界面 + 管理后台）
- 部署：Docker Compose 一键私有化交付

详细规划见 [产品需求与开发方案.md](./产品需求与开发方案.md)。

## 当前进度

- 🚧 **阶段 1 进行中（TDD）**：`backend/` 已完成 数据模型 9 表（§3.3）、检索内核、FastAPI 服务层（知识库/上传入库/分块预览/检索/带引用问答+未命中兜底/会话历史）、多格式解析与异步流水线（txt/md/docx/xlsx/pptx/pdf，扫描件路由 MinerU，ARQ 可选）、**模型网关**（`model_configs` 分场景路由+fallback 链、BYO-key Fernet 加密存储打码不回传、`/api/models` 后台 CRUD、用量台账与 `/api/usage/summary` 看板简版）——`cd backend && pytest` 93 passed + 18 子测试绿，`pytest -m smoke` 真机全链路通过
- ✅ **阶段 1 前端完成（2026-09-20，任务 7 七子任务收口）**：`frontend/`（Next.js 15 App Router + Tailwind/shadcn 基础件）四页——`/` 聊天（会话列表/消息流/[n] 引用内联 chip→原文抽屉，零额外请求）、`/admin/kb`（建库/上传/入库状态 3s 轮询至 就绪|失败/reprocess 重试/chunks 抽屉）、`/admin/models`（key 掩码列表/登记必填校验/启停 PATCH/删除/503 透传）、`/admin/usage`（用量看板简版）；根布局统一顶部导航。启动：`cd frontend && npm run dev`（:3000），**rewrite 代理** `/api/v1/:path*` → `http://127.0.0.1:8000/api/v1/:path*`（需后端在跑，同源免 CORS）。契约消费：一律经 `@umax/sdk-ts` 强类型 client（`src/lib/api.ts` 收口，禁裸 fetch），DTO/路径常量与 `contracts/openapi.json` 对齐。测试：`cd frontend && npm test`（Vitest+RTL 19 用例，含 multipart 文件名、错误映射、轮询终止、引用必锁）+ `npm run typecheck` + `npm run build` 全绿。真机冒烟（建库→上传中文 md→就绪→带引用问答→用量计数→models 503）记录见需求文档 §6.5-9
- ✅ **管理鉴权（2026-09-22，轻量方案）**：`ADMIN_TOKEN` 配置后管理类端点（知识库写操作/模型全部/用量全部）要求登录会话——`POST /api/v1/admin/login` 校验口令（常数时间比对）下发 HttpOnly+SameSite=Lax cookie（7 天）+ JS 可读 `admin_hint` 标记，`POST /api/v1/admin/logout` 两者齐清；留空=不启用（开发默认，启动日志 warning）。前端普通访客只见聊天（Nav 按标记显隐管理入口，`/admin/*` 直访弹回 `/admin/login` 口令页）+ 三管理页 401 横幅"去登录"兜底。聊天与读取路径保持开放。已知取舍：共享口令无用户区分/审计，用户体系+RBAC 押后
- ✅ **皮肤切换（2026-09-22）**：Nav 右侧「浅色/深色/护眼」三主题，语义 token 变量组（`.dark`/`.sepia` 覆盖 `--card/--brand` 等，业务组件零写死色）；选择存 `umax_theme` cookie，root layout SSR 直出 `<html class>`——首帧即正确皮肤、无 hydration 错位、无刷新闪白
- ✅ **UI 设计规范落地（2026-09-23）**：全站统一 Inter（中文回落系统）+ Consolas 等宽；五级字阶 `text-h1/h2/h3/body/caption`（24/32、18/24、16/22、14/20+0.2px 字距、12/18）在 `@theme` 注册锁死，组件层禁默认字号/手动行高/700+ 字重（`styleGuard.test.ts` 静态扫描守护）；文字固定三层级 `--ink-1/2/3`（浅色=#1D2129/#4E5969/#86909C，深/护按层级配等价三色）；卡片内边距 20/24、模块间距 24、表单标签 H3、表头 16/500、数字列 500、引用标记 12px 辅色等宽、长文本列限宽不铺满
- ✅ **阶段 0 完成（2026-09-19）**：金标准 20 题，纯 BM25 基线 20/20，混合检索 20/20（百炼 API 与本地 bge 双 provider 各验一轮）；执行记录与交接说明见[产品需求与开发方案.md](./产品需求与开发方案.md)第六章
- 链路：切块 → pgvector → BM25+向量 RRF → 重排 → qwen3.7-flash 带引用生成；嵌入/重排支持百炼 API 与本地双 provider，可 `.env` 切换
- 用法：`docker compose up -d` → `cd backend && ../.venv/Scripts/python.exe -m pytest`（回归）/ `python -m app.main`（起服务）；stage0 验证脚本已归档至 `archive/stage0/`（勿再对真库跑其 ingest）
- 当前配置：百炼已放行全部模型，`EMBED_PROVIDER=bailian`（qwen3.7-text-embedding 1024 维 + qwen3.7-text-rerank），已重新入库；chat 间歇性 403 已内置重试
- 下一步：阶段 1 收尾项按需推进（用户体系+初始化向导（一期已上单管理员口令版）、Docker Compose 交付打包、评测集语料扩容）；MinerU 镜像与 redis 容器按需启动（`QUEUE_BACKEND=arq` 切换异步入库）

## 契约工作流

唯一事实源：`contracts/openapi.json`（入库，由漂移测试守护）。**改端点必须走这四步**：

1. 先改/加测试（红）——在 `backend/tests/` 契约相关测试里先锁定新行为
2. 实现端点变更（后端代码）
3. 重新导出 spec：`cd backend && ../.venv/Scripts/python.exe scripts/export_openapi.py`
4. 双端验证：`cd backend && ../.venv/Scripts/python.exe -m pytest` + `cd sdk-ts && npm test`（gen → **新鲜度闸** `git diff --exit-code src/schema.d.ts` → tsc；gen 会覆盖工作区再比对，若改了 `openapi.json` 却忘了把重新生成的 `schema.d.ts` 一起提交，npm test 直接红——入库类型永不静默腐烂）

前端一律经 `@umax/sdk-ts`（`sdk-ts/`，openapi-fetch 强类型客户端）访问接口，**禁止裸 fetch `/api/v1`**——URL、参数、响应类型全部在编译期由 `schema.d.ts` 校验。

可选拦截 breaking change：安装 [oasdiff](https://github.com/oasdiff/oasdiff) 后执行 `oasdiff breaking <上个提交的 contracts/openapi.json> contracts/openapi.json`（个人项目一期以 git diff 评审 spec 变更为主，不强制装二进制）。

已知取舍：`scenario` 字段在 spec 中以 `pattern` 约束，TS SDK 里呈现为 `string` 而非字面量联合类型——刻意为之，SDK v1 时再收紧。
