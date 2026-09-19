# 前端（任务 7）设计 — Next.js 聊天 + 引用溯源 + 管理后台

**日期：** 2026-09-20　**状态：** 用户已确认（"确认，按此设计推进"）
**需求来源：** `产品需求与开发方案.md` §⑥ 前端、阶段 1 任务 7

## 目标

新建 `frontend/` 单项目，承载聊天界面（含引用溯源）与管理后台（知识库/模型/用量），全部数据经 `sdk-ts` 类型化客户端访问 `/api/v1`，禁裸 fetch。

## 已锁定决策（不在本设计重开）

- Next.js + shadcn/ui，聊天与后台同一项目（需求文档 §⑥）。
- 消息渲染走结构化 content parts（`[{type:text},...]`），不按纯字符串处理（需求文档 §⑥）。
- 测试策略：Vitest 组件测试为主（2026-09-19 用户选定，见记忆 feedback-tdd-mode）。
- 数据获取模式：轻量 hooks + sdk-ts，不引 TanStack Query / RSC 取数（2026-09-20 用户选定）。
- 后端契约冻结：改动前端需求一律以现有 17 个端点为边界；改端点走 README 契约工作流四步。

## 1. 工程与链路

- 位置：仓库顶层 `frontend/`，与 `backend/`、`sdk-ts/` 平级；Next.js 15 App Router，TypeScript strict。
- 依赖：`"@umax/sdk": "file:../sdk-ts"` —— SDK 类型即契约类型；sdk-ts 的 gen-diff 新鲜度门禁同时保护前端。
- 开发代理：`next.config` rewrite `/api/v1/:path*` → `http://127.0.0.1:8000/api/v1/:path*`。SDK 默认 baseUrl `/api/v1` 零配置直通，无 CORS。
- 生产：nginx 同域反代，同构配置。

## 2. 页面结构（4 页）

### `/` 聊天
- 左栏：会话列表（`GET /conversations`）+ 新建会话。
- 主区：消息流（`GET /conversations/{conv_id}/messages`，按 content parts 渲染）；底部输入框 → `POST /chat`（带 conversation_id 续话，缺省新会话）。
- 引用溯源：答案 `[n]` 标记 + citation chips（doc_name）；点击开右侧抽屉展示该引用的原文片段。chat 响应的 `citations` 自带 chunk 级信息（n/doc_name/chunk_id/片段文本/得分），抽屉直接渲染，不发额外请求。
- 发送中：loading 态（后端一次性 JSON，无流式）。

### `/admin/kb` 知识库
- kb 列表 + 新建（`GET/POST /kb`）。无删除端点——不做删除按钮。
- 按 kb 上传文档（`POST /kb/{kb_id}/documents`，multipart）。
- 文档列表轮询 ingest 状态（ARQ 异步）：存在 pending 时每 3s 轮询，全部 done/failed 停止；failed 行内展示错误并给 reprocess（`POST /documents/{doc_id}/reprocess`）。
- 文档详情抽屉：`GET /documents/{doc_id}` + `GET /documents/{doc_id}/chunks` 分页查看切块。

### `/admin/models` 模型配置
- 列表（`GET /models`，key 恒为 `****后4位` 掩码，明文不出后端，前端不尝试还原）。
- 登记表单（`POST /models`：厂商/base_url/api_key/模型名/scenario 能力标签/是否默认/fallback 顺序）；scenario 的 TS 类型是 `string`（spec 用 pattern），表单用下拉限定为后端 SCENARIOS 集合。
- patch（启用/排序/换 key）与删除（`PATCH/DELETE /models/{model_id}`）。

### `/admin/usage` 用量
- `GET /usage/summary`：scenario×model 聚合表 + 顶部汇总卡（调用数、tokens 合计）。

## 3. 数据层（`lib/`，约 80 行）

- `lib/api.ts`：`export const api = createApiClient()` 单例；类型 re-export。
- `lib/hooks.ts`：
  - `useAsync<T>(fn, deps)` → `{data, error, loading, reload}`；
  - `usePolling<T>(fn, {intervalMs, stopWhen})` → 同上，命中停止条件即清定时器。
- 错误处理：openapi-fetch 返回 `{data, error}`；error 体为契约声明的 `ErrorOut {code, message}` → 统一渲染 toast/错误条；网络层失败（error 体缺失）降级为通用错误文案。禁止吞错。

## 4. 测试（Vitest 为主）

- 栈：Vitest + @testing-library/react + jsdom。
- 可测性设计：组件经 props/context 接收 api 实例（默认单例），测试注入手写假 client —— 类型由 sdk-ts 契约推导，stub 不会跑偏；不引 MSW。
- 必锁行为：
  1. 发消息 → 渲染答案 + `[n]` 引用 → 点开引用抽屉显示片段；
  2. 文档列表 pending→done 轮询并按终态停止（假定时器）；
  3. key 掩码 `****` 原样展示；
  4. 模型表单 scenario 下拉与必填校验；
  5. ErrorOut 渲染 message、网络失败渲染通用文案。
- 回归：`cd frontend && npm test && npm run typecheck` 全绿；`next build` 通过为完成标准之一。
- 不引 E2E 框架。

## 5. 边界与 YAGNI

- 一期无鉴权（与后端一致）；无流式渲染；不做租户切换 UI。
- 不实现契约没有的功能（kb 删除、chunk 编辑等）。
- 上传约束文案与后端一致（大小/格式上限以 422/400 声明为准，前端只做提示不做二次校验逻辑复制）。

## 6. 验收

- 真后端（8000）+ 真 PG 下：新会话提问得到带引用的答案；上传中文 PDF 状态轮询到 done；后台能登记/停用模型；用量页有数。
- Vitest 组件测试覆盖上述 5 项必锁行为；typecheck 与 build 通过。
