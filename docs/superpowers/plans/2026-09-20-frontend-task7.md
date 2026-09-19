# 前端任务 7（Next.js 聊天 + 管理后台）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `frontend/`：聊天（含引用溯源）+ 管理后台（知识库/模型/用量），全部经 `sdk-ts` 类型化客户端访问 `/api/v1`。

**Architecture:** 每个页面 = 一个接收 `api: Client` 的纯客户端组件（page.tsx 只做薄挂载），数据层为 `lib/api.ts`（unwrap/错误映射）+ `lib/hooks.ts`（useAsync/usePolling），无状态库。开发期 Next rewrite 代理到 127.0.0.1:8000，SDK 默认 baseUrl 直通无 CORS。

**Tech Stack:** Next.js 15 (App Router, TS strict) + Tailwind + shadcn/ui 基础件 + @umax/sdk-ts（file: 依赖）+ Vitest/@testing-library/react/jsdom。

**Spec:** `docs/superpowers/specs/2026-09-20-frontend-design.md`（用户已确认）

## Global Constraints

- 禁裸 fetch：网络出口只有 `lib/api.ts` 的 openapi-fetch client；组件一律通过 props 接收 `Client`，测试注入假 client。
- 契约边界：只用现有端点 + 任务 1 新增的 `GET /api/v1/kb/{kb_id}/documents`；任务 1 必须走 README 契约工作流四步（实现+测试 → 错误声明 → `python scripts/export_openapi.py` 重导 → `cd sdk-ts && npm test`）。
- openapi-fetch 调用规范（全计划一致，假 client 的 URL 存根按此匹配）：路径参数一律传**模板串 + params**，如 `api.GET("/kb/{kb_id}/documents", { params: { path: { kb_id } } })`；fakeApi 收到的 url 是模板字面量（`"/kb/{kb_id}/documents"`），不是拼装后的路径。
- 后端 200 响应在 spec 里是 `unknown`（未建 response_model）：前端 DTO 类型集中在 `lib/types.ts`，出口处 `as` 一次；错误体读 `detail`（`ErrorOut = {detail: string}`；422 的 detail 是数组 → 通用文案）。
- 文档状态机：`pending → parsing → ready / failed`（backend/app/models）。轮询停止条件 = 无 pending/parsing。
- chat 响应 `citations` 自带 `{n, doc_name, chunk_id, excerpt}`，引用抽屉零额外请求。模型 key 恒为 `api_key_masked`（`****后4位`），前端不尝试还原。一期无鉴权、无流式。
- 前端回归：`cd frontend && npm test && npm run typecheck` 每任务全绿；收口加 `npm run build`。后端回归 `cd backend && ../.venv/Scripts/python.exe -m pytest` 保持全绿。
- 环境：Windows + Git Bash；Node v24；npm 直连可用；后端起服务 = `cd backend && ../.venv/Scripts/python.exe -m app.main`（127.0.0.1:8000，需 PG 容器在跑）。
- 提交节奏：每任务一个 commit（用户"提交"口令另行确认前不 push）。commit 前 `git status` 核实（IDE 有抢提交前科）。

## 文件结构（全计划落点）

```
backend/app/main.py                      改：+GET /api/v1/kb/{kb_id}/documents
backend/tests/test_api.py                改：+2 测试
backend/tests/test_contract_declared.py  改：+EXPECTED 行
contracts/openapi.json                   重导
sdk-ts/src/client.ts                     改：createApiClient 透传 options（可注入 fetch）
sdk-ts/src/schema.d.ts                   再生成
frontend/                                新建（Next 脚手架 + 下列手写文件）
  next.config.ts                         rewrite + transpilePackages
  vitest.config.ts / vitest.setup.ts
  src/lib/types.ts                       全部 DTO
  src/lib/api.ts                         Client 类型 / call / callVoid / errText / uploadDocument
  src/lib/hooks.ts                       useAsync / usePolling
  src/components/ErrorBanner.tsx
  src/components/ChatApp.tsx             src/components/__tests__/ChatApp.test.tsx
  src/components/KbAdmin.tsx             src/components/__tests__/KbAdmin.test.tsx
  src/components/ModelsAdmin.tsx         src/components/__tests__/ModelsAdmin.test.tsx
  src/components/UsageAdmin.tsx          src/components/__tests__/UsageAdmin.test.tsx
  src/components/__tests__/api-hooks.test.tsx   （lib 层锁定测试）
  src/app/layout.tsx                     顶部导航
  src/app/page.tsx                       /            → ChatApp
  src/app/admin/kb/page.tsx                            → KbAdmin
  src/app/admin/models/page.tsx                        → ModelsAdmin
  src/app/admin/usage/page.tsx                         → UsageAdmin
```

---

### Task 1: 后端契约补口 —— `GET /api/v1/kb/{kb_id}/documents`（TDD + 四步工作流）

前端文档列表/轮询没有列端点可用（17 端点里只有单文档 GET），此为设计 §2 的契约缺口，本任务补齐。

**Files:**
- Modify: `backend/app/main.py`（`list_kb` 之后、约 221 行处加端点）
- Test: `backend/tests/test_api.py`、`backend/tests/test_contract_declared.py`
- Regenerate: `contracts/openapi.json`、`sdk-ts/src/schema.d.ts`
- Modify: `sdk-ts/src/client.ts`（options 透传，同批提交）

**Interfaces:**
- Produces: `GET /api/v1/kb/{kb_id}/documents` → `DocOut[]`（`[{id, kb_id, name, status, error, size_bytes}]`，按 id 升序）；404 知识库不存在。`createApiClient(baseUrl?, options?)`，options 可含 `fetch`（测试注入）。

- [ ] **Step 1: 写失败测试**（`backend/tests/test_api.py` 末尾追加；复用既有 `app_client` fixture 与 `_upload` helper）

```python
def test_list_documents_per_kb(app_client):
    kb, r = app_client.post("/api/v1/kb", json={"name": "库A"}), None
    kb_id = kb.json()["id"]
    kb2 = app_client.post("/api/v1/kb", json={"name": "库B"}).json()["id"]
    for i in range(2):
        r = app_client.post(f"/api/v1/kb/{kb_id}/documents",
                            files={"file": (f"n{i}.txt", b"hello world content", "text/plain")})
        assert r.status_code == 201
    listed = app_client.get(f"/api/v1/kb/{kb_id}/documents")
    assert listed.status_code == 200
    rows = listed.json()
    assert [d["name"] for d in rows] == ["n0.txt", "n1.txt"]
    assert rows[0]["kb_id"] == kb_id and rows[0]["status"] == "ready"
    assert app_client.get(f"/api/v1/kb/{kb2}/documents").json() == []
    assert app_client.get("/api/v1/kb/99999/documents").status_code == 404
```

（fixture TRUNCATE 隔离保证序号从 1 起、`[]` 与 404 两个断言分别覆盖空库与不存在库。）

- [ ] **Step 2: 跑到红**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_api.py::test_list_documents_per_kb -v`
Expected: FAIL —— 404（新路径不存在）而非断言里的 200

- [ ] **Step 3: 实现**（`main.py`，紧跟 `list_kb` 函数之后；错误声明走既有 `_ERR`）

```python
    @app.get("/api/v1/kb/{kb_id}/documents",
             responses=_ERR(404, "知识库不存在"))
    def list_documents(kb_id: PathId, session: Session = Depends(get_session)):
        if not session.get(KnowledgeBase, kb_id):
            raise HTTPException(404, "知识库不存在")
        return [_doc_json(d) for d in session.query(Document)
                .filter_by(kb_id=kb_id).order_by(Document.id)]
```

- [ ] **Step 4: 声明表 + 全量后端回归**

`backend/tests/test_contract_declared.py` 的 `EXPECTED` 增加一行（放在 post 同 path 行下面）：

```python
    ("/api/v1/kb/{kb_id}/documents", "get"): {"404"},
```

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest`
Expected: 93 passed + 17 fuzz 子测试（新端点自动进 fuzz/drift 面）

- [ ] **Step 5: sdk-ts client options 透传**（`sdk-ts/src/client.ts` 整文件替换为下面内容——测试要注入 fetch 验证 multipart 序列化，必须能换 fetch 实现）

```ts
// sdk-ts/src/client.ts
// 契约驱动生成：schema.d.ts 来自 openapi.json（npm run gen），手写只有这个薄封装
import createClient, { type ClientOptions } from "openapi-fetch";
import type { paths } from "./schema.js";

export function createApiClient(baseUrl = "/api/v1", options?: Partial<ClientOptions>) {
  return createClient<paths>({ baseUrl, ...options });
}
export type { paths };
```

- [ ] **Step 6: 重导 spec + 再生成 SDK + 全绿**

```bash
cd backend && ../.venv/Scripts/python.exe scripts/export_openapi.py
cd ../sdk-ts && npm test   # gen → schema.d.ts diff 干净（改动已提交前会红——先 npm run gen 再 test）→ typecheck 0 错误
```

注意：`npm test` 内含 `git diff --exit-code`，本步先 `npm run gen` 落新 schema 再跑 `npm test` 验证。

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py backend/tests/test_contract_declared.py contracts/openapi.json sdk-ts/src/client.ts sdk-ts/src/schema.d.ts
git commit -m "stage1 任务7-1: 契约补口 GET /kb/{kb_id}/documents（TDD+四步工作流）；sdk-ts client 支持注入 fetch"
```

---

### Task 2: `frontend/` 脚手架与测试基建

**Files:**
- Create: `frontend/`（create-next-app 产物）+ 手写 `next.config.ts`、`vitest.config.ts`、`vitest.setup.ts`
- Modify: `frontend/package.json`（scripts + 依赖）

**Interfaces:**
- Consumes: `@umax/sdk-ts`（file: 依赖，`createApiClient`）
- Produces: 可跑 `npm test`（Vitest+RTL+jsdom）、`npm run typecheck`、`npm run build` 的空页面骨架；dev 代理 rewrite。

- [ ] **Step 1: 脚手架**（仓库根目录）

```bash
npx --yes create-next-app@15 frontend --typescript --tailwind --eslint --app --src-dir --import-alias "@/*" --use-npm --yes
```

- [ ] **Step 2: 依赖**

```bash
cd frontend
npm i "@umax/sdk-ts@file:../sdk-ts" openapi-fetch@^0.17.0
npm i -D vitest@^3 @vitejs/plugin-react@^4 jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom
```

- [ ] **Step 3: 失败测试（管线冒烟）** —— `src/components/__tests__/smoke.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("test pipeline", () => {
  it("renders jsx in jsdom", () => {
    render(<p>umax-frontend-ready</p>);
    expect(screen.getByText("umax-frontend-ready")).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: 配置文件，跑到绿**

`vitest.config.ts`：

```ts
import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", setupFiles: ["./vitest.setup.ts"], globals: false },
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
});
```

`vitest.setup.ts`：

```ts
import "@testing-library/jest-dom/vitest";
```

`next.config.ts` 整体替换：

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@umax/sdk-ts"],
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: "http://127.0.0.1:8000/api/v1/:path*" }];
  },
};

export default nextConfig;
```

`package.json` scripts 增加：`"test": "vitest run"`、`"typecheck": "tsc --noEmit"`。
若 `tsc --noEmit` 报 `@umax/sdk-ts` 解析失败：给 `sdk-ts/package.json` 加 `"exports": { ".": "./src/client.ts" }` 后重跑。

Run: `npm test && npm run typecheck`
Expected: smoke 1 passed；typecheck 0 错误

- [ ] **Step 5: shadcn 基础件**

```bash
npx --yes shadcn@2 init -y -b neutral
npx --yes shadcn@2 add button input badge card -y
```

若 registry 不可达：手写 `src/lib/utils.ts`（clsx+tailwind-merge 的 `cn`）与 `src/components/ui/{button,input,badge,card}.tsx` 四个最小等价件（className 变体用 `class-variance-authority`，`npm i class-variance-authority clsx tailwind-merge lucide-react`），保持 import 面 `@/components/ui/*` 不变。

- [ ] **Step 6: build 验证 + Commit**

```bash
npm run build   # 脚手架默认页 0 错误
cd .. && git status   # 核实无越界改动后：
git add frontend sdk-ts package.json 2>/dev/null; git add frontend
git commit -m "stage1 任务7-2: Next.js 15 前端脚手架（rewrite 代理 + Vitest/RTL 管线 + shadcn 基础件 + sdk-ts 接线）"
```

---

### Task 3: 数据层 `lib/`（types / api / hooks / ErrorBanner，TDD）

**Files:**
- Create: `frontend/src/lib/types.ts`、`frontend/src/lib/api.ts`、`frontend/src/lib/hooks.ts`、`frontend/src/components/ErrorBanner.tsx`
- Test: `frontend/src/components/__tests__/api-hooks.test.tsx`

**Interfaces:**
- Consumes: `createApiClient`（Task 1 版）
- Produces（后续所有组件依赖，签名冻结）：
  - `type Client`（GET/POST/PATCH/DELETE 四方法）；`api: Client` 单例
  - `call<T>(p): Promise<T>`（error→throw ApiError）、`callVoid(p)`、`errText(body): string`
  - `uploadDocument(client: Client, kbId: number, file: File): Promise<DocOut>`
  - `useAsync<T>(fn, deps?) → {data?, error?, loading, reload()}`
  - `usePolling<T>(fn, {intervalMs, stopWhen, enabled}) → 同上`
  - `fakeApi(stubs)` / `ok(data)` / `fail(status, detail)`（`src/lib/testkit.ts`，测试公用，非生产代码）

- [ ] **Step 1: 写失败测试** —— `src/components/__tests__/api-hooks.test.tsx`

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createApiClient } from "@umax/sdk-ts";
import { describe, expect, it, vi } from "vitest";
import { call, errText, uploadDocument, type Client } from "@/lib/api";
import { useAsync, usePolling } from "@/lib/hooks";
import { fakeApi, ok } from "@/lib/testkit";

describe("errText", () => {
  it("maps ErrorOut.detail / 422 array / network failure", () => {
    expect(errText({ detail: "知识库不存在" })).toBe("知识库不存在");
    expect(errText({ detail: [{ loc: ["body"] }] })).toContain("字段校验");
    expect(errText(undefined)).toContain("网络");
  });
});

describe("uploadDocument", () => {
  it("sends real FormData (multipart), not JSON", async () => {
    let captured: RequestInit | undefined;
    const fakeFetch = (async (_url: string, init?: RequestInit) => {
      captured = init ?? {};
      return new Response(JSON.stringify({ id: 7, kb_id: 1, name: "a.txt",
        status: "pending", error: null, size_bytes: 3 }),
        { status: 201, headers: { "content-type": "application/json" } });
    }) as unknown as typeof fetch;
    const client = createApiClient("/api/v1", { fetch: fakeFetch }) as Client;
    const doc = await uploadDocument(client, 1, new File(["abc"], "a.txt", { type: "text/plain" }));
    expect(captured!.body).toBeInstanceOf(FormData);
    expect((captured!.body as FormData).get("file")).toBeInstanceOf(File);
    expect(doc.id).toBe(7);
  });
});

function Probe({ fn }: { fn: () => Promise<string> }) {
  const { data, loading, reload } = useAsync(fn);
  return <button onClick={reload}>{loading ? "loading" : String(data)}</button>;
}

it("useAsync resolves and reload re-runs", async () => {
  let n = 0;
  render(<Probe fn={async () => `v${++n}`} />);
  expect(screen.getByText("loading")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("v1")).toBeInTheDocument());
  await userEvent.click(screen.getByText("v1"));
  await waitFor(() => expect(screen.getByText("v2")).toBeInTheDocument());
});

function Poller() {
  const { data } = usePolling(async () => ++counter, { intervalMs: 1000, stopWhen: (v) => v >= 3, enabled: true });
  return <p>{String(data)}</p>;
}
let counter = 0;

it("usePolling stops at terminal value", async () => {
  vi.useFakeTimers();
  render(<Poller />);
  await vi.advanceTimersByTimeAsync(0);   // flush first (immediate) fetch
  expect(screen.getByText("1")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(screen.getByText("2")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(screen.getByText("3")).toBeInTheDocument();
  counter = 99;                            // 若未停止，下一轮会显示 99
  await vi.advanceTimersByTimeAsync(3000);
  expect(screen.getByText("3")).toBeInTheDocument();
  vi.useRealTimers();
});

it("fakeApi routes through call() unwrapping", async () => {
  const api = fakeApi({ GET: async (url: string) => (url === "/kb" ? ok([{ id: 1 }]) : undefined) });
  await expect(call(api.GET("/kb"))).resolves.toEqual([{ id: 1 }]);
});
```

- [ ] **Step 2: 跑到红**

Run: `cd frontend && npx vitest run src/components/__tests__/api-hooks.test.tsx`
Expected: 模块不存在，全部用例 FAIL

- [ ] **Step 3: 实现 `src/lib/types.ts`**（逐字段对照 backend/app/main.py 的 `_doc_json`/`_model_json`/chat/usage 返回）

```ts
export type DocStatus = "pending" | "parsing" | "ready" | "failed";
export interface KbOut { id: number; name: string; description: string | null }
export interface DocOut { id: number; kb_id: number; name: string; status: DocStatus; error: string | null; size_bytes: number }
export interface ChunkOut { id: number; chunk_index: number; content: string; has_embedding: boolean }
export interface Citation { n: number; doc_name: string; chunk_id: number; excerpt: string }
export interface ChatOut { conversation_id: number; answer: string; citations: Citation[]; cited_docs: string[]; usage: { prompt_tokens: number; completion_tokens: number } }
export interface ConversationOut { id: number; title: string; kb_ids: number[] }
export interface MessageOut { id: number; role: "user" | "assistant"; content: { type: string; text?: string }[]; citations: Citation[] | null }
export interface ModelOut { id: number; scenario: string; provider: string; base_url: string; model_name: string; capabilities: Record<string, unknown>; is_default: boolean; fallback_rank: number; enabled: boolean; api_key_masked: string }
export interface UsageOut { scenario: string; model: string; calls: number; prompt_tokens: number; completion_tokens: number }
```

- [ ] **Step 4: 实现 `src/lib/api.ts`**

```ts
import { createApiClient } from "@umax/sdk-ts";
import type { DocOut } from "./types";

export type Client = Pick<ReturnType<typeof createApiClient>, "GET" | "POST" | "PATCH" | "DELETE">;
export const api: Client = createApiClient();

export class ApiError extends Error {
  constructor(public body: unknown) {
    super(errText(body));
  }
}

export function errText(body: unknown): string {
  if (typeof body === "string") return body;
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "请求不合法（字段校验失败）";
  return "网络或服务异常，请稍后重试";
}

export async function call<T>(p: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await p;
  if (error !== undefined) throw new ApiError(error);
  if (data === undefined) throw new ApiError(undefined);
  return data;
}

export async function callVoid(p: Promise<{ data?: unknown; error?: unknown }>): Promise<void> {
  const { error } = await p;
  if (error !== undefined) throw new ApiError(error);
}

export async function uploadDocument(client: Client, kbId: number, file: File): Promise<DocOut> {
  const form = new FormData();
  form.append("file", file, file.name);
  // 后端 200 未建 response_model（spec 里是 unknown），DTO 断言集中在此
  return (await call(client.POST("/kb/{kb_id}/documents",
    { params: { path: { kb_id: kbId } }, body: form as never }))) as unknown as DocOut;
}
```

- [ ] **Step 5: 实现 `src/lib/hooks.ts`**

```ts
import { useEffect, useState } from "react";

export interface AsyncState<T> { data?: T; error?: unknown; loading: boolean }

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<AsyncState<T>>({ loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    fn().then((data) => alive && setState({ data, loading: false }))
        .catch((error) => alive && setState({ error, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

export function usePolling<T>(fn: () => Promise<T>,
  opts: { intervalMs: number; stopWhen: (v: T) => boolean; enabled: boolean }) {
  const [state, setState] = useState<AsyncState<T>>({ loading: false });
  const [tick, setTick] = useState(0);
  const { intervalMs, stopWhen, enabled } = opts;
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const v = await fn();
        if (!alive) return;
        setState({ data: v, loading: false });
        if (!stopWhen(v)) timer = setTimeout(run, intervalMs);
      } catch (error) {
        if (alive) setState({ error, loading: false });
      }
    };
    setState({ loading: true });
    run();
    return () => { alive = false; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, enabled]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}
```

- [ ] **Step 6: 实现 `src/components/ErrorBanner.tsx` + 测试工具 `src/lib/testkit.ts`**

```tsx
import { errText } from "@/lib/api";

export function ErrorBanner({ error }: { error?: unknown }) {
  if (!error) return null;
  return <div role="alert" className="rounded bg-red-50 p-2 text-sm text-red-700">{errText(error)}</div>;
}
```

```ts
// src/lib/testkit.ts —— 仅测试使用的假 client（真实类型来自契约）
import type { Client } from "@/lib/api";

export const ok = (data: unknown) =>
  Promise.resolve({ data, error: undefined, response: new Response() });
export const fail = (detail: string, status = 400) =>
  Promise.resolve({ data: undefined, error: { detail }, response: new Response("x", { status }) });

export function fakeApi(
  stubs: Partial<Record<keyof Client, (url: string, init?: unknown) => unknown>>,
): Client {
  const missing = (url: string, m: string) => { throw new Error(`fakeApi: 未存根 ${m} ${url}`); };
  return {
    GET: ((u: string, i?: unknown) => stubs.GET?.(u, i) ?? missing(u, "GET")),
    POST: ((u: string, i?: unknown) => stubs.POST?.(u, i) ?? missing(u, "POST")),
    PATCH: ((u: string, i?: unknown) => stubs.PATCH?.(u, i) ?? missing(u, "PATCH")),
    DELETE: ((u: string, i?: unknown) => stubs.DELETE?.(u, i) ?? missing(u, "DELETE")),
  } as unknown as Client;
}
```

- [ ] **Step 7: 全绿 + Commit**

Run: `cd frontend && npx vitest run && npm run typecheck`
Expected: smoke+lib 全 passed，typecheck 0 错误

```bash
git add frontend/src
git commit -m "stage1 任务7-3: 前端数据层（契约 DTO/错误映射/useAsync/usePolling/multipart 锁定/假 client）"
```

---

### Task 4: 聊天页 `/`（会话 + 消息流 + 引用溯源）

**Files:**
- Create: `frontend/src/components/ChatApp.tsx`、`frontend/src/app/page.tsx`（替换脚手架 page）
- Test: `frontend/src/components/__tests__/ChatApp.test.tsx`

**Interfaces:**
- Consumes: Task 3 全部
- Produces: `<ChatApp api={Client} />`；`GET/POST /chat`、`/conversations`、`/conversations/{id}/messages` 的调用形态（URL 串以 `/conversations`、`/chat`、`/conversations/${id}/messages` 为准，Task 5-6 同模式）。

- [ ] **Step 1: 写失败测试** —— `ChatApp.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import ChatApp from "@/components/ChatApp";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { ChatOut, ConversationOut, MessageOut } from "@/lib/types";

const convs: ConversationOut[] = [{ id: 1, title: "退货政策", kb_ids: [] }];
const history: MessageOut[] = [
  { id: 1, role: "user", content: [{ type: "text", text: "退货几天？" }], citations: null },
  { id: 2, role: "assistant", content: [{ type: "text", text: "据资料，退货需7天响应[1]。" }],
    citations: [{ n: 1, doc_name: "运维手册.md", chunk_id: 9, excerpt: "退货窗口为 7 个自然日" }] },
];
const chatOut: ChatOut = {
  conversation_id: 1, answer: "需24小时响应[1]。",
  citations: [{ n: 1, doc_name: "运营手册.txt", chunk_id: 42, excerpt: "售后响应承诺：24小时" }],
  cited_docs: ["运营手册.txt"], usage: { prompt_tokens: 100, completion_tokens: 20 },
};

const api = fakeApi({
  GET: async (url) => (url === "/conversations" ? ok(convs)
    : url === "/conversations/{conv_id}/messages" ? ok(history) : undefined),
  POST: async () => ok(chatOut),
});

it("renders history and opens cite drawer without extra request", async () => {
  render(<ChatApp api={api} />);
  await userEvent.click(screen.getByText("退货政策"));
  expect(await screen.findByText(/退货需7天响应/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /\[1\] 运维手册\.md/ }));
  expect(await screen.findByText("退货窗口为 7 个自然日")).toBeInTheDocument();
});

it("asks question, shows inline citation chip, allows second question", async () => {
  const asked: unknown[] = [];
  const api2 = fakeApi({
    GET: async (url) => (url === "/conversations" ? ok(convs)
      : url === "/conversations/{conv_id}/messages" ? ok(history) : undefined),
    POST: async (url, init) => {
      asked.push((init as { body: { question: string } }).body.question);
      return ok(chatOut);
    },
  });
  render(<ChatApp api={api2} />);
  await userEvent.type(screen.getByLabelText("提问"), "售后多久响应？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  const chip = await screen.findByRole("button", { name: /\[1\] 运营手册\.txt/ });
  await userEvent.click(chip);
  expect(await screen.findByText("售后响应承诺：24小时")).toBeInTheDocument();
  expect(asked).toEqual(["售后多久响应？"]);
  // 第二条可继续提问（发送锁必须释放）
  await userEvent.type(screen.getByLabelText("提问"), "换货呢？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(asked).toEqual(["售后多久响应？", "换货呢？"]);
});

it("renders backend detail in banner when list fails", async () => {
  const api3 = fakeApi({ GET: async () => fail("知识库不存在", 404) });
  render(<ChatApp api={api3} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("知识库不存在");
});
```

- [ ] **Step 2: 跑到红**（`npx vitest run src/components/__tests__/ChatApp.test.tsx`，模块不存在 FAIL）

- [ ] **Step 3: 实现 `src/components/ChatApp.tsx`**

```tsx
"use client";
// 聊天页：左栏会话、主区消息流、引用 [n] → chips → 右侧原文抽屉（零额外请求）
// 取数模型：只有"点击会话"才拉历史；send() 的结果存本地 turns 追加渲染——
// 避免"新会话 send 后 refetch → 本地+远端同一答案渲染两遍"。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, type Client } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { ChatOut, Citation, ConversationOut, MessageOut } from "@/lib/types";

function answerParts(answer: string, citations: Citation[], onCite: (c: Citation) => void) {
  return answer.split(/(\[\d+\])/g).map((seg, i) => {
    const m = seg.match(/^\[(\d+)\]$/);
    const c = m ? citations.find((x) => x.n === Number(m[1])) : undefined;
    if (!c) return <span key={i}>{seg}</span>;
    return (
      <button key={i} className="mx-0.5 rounded bg-blue-50 px-1 text-xs text-blue-700 hover:bg-blue-100"
              onClick={() => onCite(c)} title={c.excerpt}>
        {`[${c.n}] ${c.doc_name}`}
      </button>
    );
  });
}

interface LocalTurn { convId: number; question: string; out: ChatOut }

export default function ChatApp({ api }: { api: Client }) {
  const [convId, setConvId] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [sendErr, setSendErr] = useState<unknown>(null);
  const [turns, setTurns] = useState<LocalTurn[]>([]);
  const [cite, setCite] = useState<Citation | null>(null);
  // activeConv 仅由点击设置——useAsync deps 变化 = 用户点了某个会话 = 拉历史
  const [activeConv, setActiveConv] = useState<number | null>(null);
  const convs = useAsync(() => call(api.GET("/conversations")) as Promise<ConversationOut[]>);
  const msgs = useAsync(
    () => (activeConv === null ? Promise.resolve([] as MessageOut[])
      : call(api.GET("/conversations/{conv_id}/messages", { params: { path: { conv_id: activeConv } } })) as Promise<MessageOut[]>),
    [activeConv]);

  function openConversation(id: number | null) {
    setConvId(id);
    setActiveConv(id);
    setTurns([]);
    setCite(null);
    setSendErr(null);
  }

  async function send() {
    const q = question.trim();
    if (!q || sending) return;
    setSending(true);
    setSendErr(null);
    try {
      const out = (await call(api.POST("/chat", {
        body: { question: q, conversation_id: convId },
      }))) as unknown as ChatOut;
      setConvId(out.conversation_id);
      setTurns((t) => [...t, { convId: out.conversation_id, question: q, out }]);
      setQuestion("");
      setCite(null);
      convs.reload();
    } catch (e) {
      setSendErr(e);
    } finally {
      setSending(false);
    }
  }

  const shown: MessageOut[] = [
    ...(msgs.data ?? []),
    ...turns.filter((t) => t.convId === convId).flatMap((t, i) => [
      { id: -1000 - i, role: "user" as const, content: [{ type: "text", text: t.question }], citations: null },
      { id: -1001 - i, role: "assistant" as const, content: [{ type: "text", text: t.out.answer }], citations: t.out.citations },
    ]),
  ];

  return (
    <div className="flex h-[calc(100vh-3rem)]">
      <aside className="w-56 shrink-0 border-r p-2">
        <Button className="w-full" variant="outline" onClick={() => openConversation(null)}>
          新建会话
        </Button>
        <ul className="mt-2 space-y-1">
          {(convs.data ?? []).map((c) => (
            <li key={c.id}>
              <button className={`w-full truncate rounded px-2 py-1 text-left text-sm hover:bg-accent ${c.id === convId ? "bg-accent" : ""}`}
                      onClick={() => openConversation(c.id)}>
                {c.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {shown.map((m, i) => (
            <div key={`${m.id}:${i}`} className={m.role === "user" ? "text-right" : ""}>
              <div className={`inline-block max-w-[80%] rounded-lg border p-3 text-left text-sm ${m.role === "user" ? "bg-muted" : ""}`}>
                {m.content.map((p, j) => p.type === "text" && (
                  <p key={j}>{m.role === "assistant" && m.citations
                    ? answerParts(p.text ?? "", m.citations, setCite)
                    : p.text}</p>
                ))}
              </div>
            </div>
          ))}
          {sending && <p className="text-sm text-muted-foreground">检索并生成中…</p>}
          <ErrorBanner error={msgs.error ?? convs.error ?? sendErr} />
        </div>
        <form className="flex gap-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <Input aria-label="提问" placeholder="就知识库内容提问…" value={question}
                 onChange={(e) => setQuestion(e.target.value)} disabled={sending} />
          <Button type="submit" disabled={!question.trim() || sending}>发送</Button>
        </form>
      </main>
      {cite && (
        <aside className="w-80 shrink-0 space-y-2 border-l bg-muted/30 p-4">
          <div className="flex items-center justify-between">
            <Badge>{`[${cite.n}] ${cite.doc_name}`}</Badge>
            <Button size="sm" variant="ghost" onClick={() => setCite(null)}>关闭</Button>
          </div>
          <p className="text-sm">chunk #{cite.chunk_id}</p>
          <blockquote className="border-l-2 pl-2 text-sm text-muted-foreground">{cite.excerpt}</blockquote>
        </aside>
      )}
    </div>
  );
}
```

实现要点（执行者必读）：
- `sending` 是发送锁，测试第二条锁"连续两问都能发出"。
- 历史只在 `openConversation` 时拉（`activeConv` deps）；send 成功后**不** reload msgs——新答案走 `turns` 本地追加，杜绝重复渲染。
- 抽屉数据全部来自消息自带的 `citations`，无任何网络请求。

- [ ] **Step 4: 跑到绿**（`npx vitest run`；随后 `npm run typecheck` 0 错误；类型报错时允许微调 cast 位置，禁改断言）

- [ ] **Step 5: 页面挂载** —— `src/app/page.tsx` 整体替换

```tsx
import ChatApp from "@/components/ChatApp";
import { api } from "@/lib/api";

export default function Home() {
  return <ChatApp api={api} />;
}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "stage1 任务7-4: 聊天页——会话列表/消息流/[n] 引用内联 chip 与原文抽屉（零额外请求）"
```

---

### Task 5: `/admin/kb` 知识库后台（上传 + 状态轮询 + 抽屉）

**Files:**
- Create: `frontend/src/components/KbAdmin.tsx`、`frontend/src/app/admin/kb/page.tsx`
- Test: `frontend/src/components/__tests__/KbAdmin.test.tsx`

**Interfaces:**
- Consumes: Task 3；`uploadDocument`（POST 出口统一走它，组件内不再拼 multipart）
- Produces: `<KbAdmin api={Client} />`

- [ ] **Step 1: 写失败测试** —— `KbAdmin.test.tsx`（轮询停止是必锁行为②）

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import KbAdmin from "@/components/KbAdmin";
import { fakeApi, ok } from "@/lib/testkit";
import type { DocOut } from "@/lib/types";

const pending: DocOut = { id: 1, kb_id: 1, name: "ops.txt", status: "pending", error: null, size_bytes: 5 };
const ready: DocOut = { ...pending, status: "ready" };

describe("KbAdmin 轮询", () => {
  it("polls while pending and stops at ready", async () => {
    let calls = 0;
    const api = fakeApi({
      GET: async (url) => {
        if (url === "/kb") return ok([{ id: 1, name: "运营库", description: null }]);
        if (url === "/kb/{kb_id}/documents") {
          calls += 1;
          return ok(calls === 1 ? [pending] : [ready]);
        }
        return undefined;
      },
    });
    vi.useFakeTimers();
    render(<KbAdmin api={api} />);
    await act(() => vi.advanceTimersByTimeAsync(0));           // 首轮（挂载即拉）
    expect(screen.getByText("排队中")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(3000));        // 第二轮 → ready
    expect(screen.getByText("就绪")).toBeInTheDocument();
    calls = 99;
    await act(() => vi.advanceTimersByTimeAsync(9000));        // 终态后不应再轮
    expect(calls).toBe(2);
    vi.useRealTimers();
  });

  it("failed doc offers reprocess", async () => {
    const failed: DocOut = { ...pending, status: "failed", error: "解析炸了" };
    const reprocess = vi.fn(async () => ok(pending));
    const api = fakeApi({
      GET: async (url) => (url === "/kb" ? ok([{ id: 1, name: "库", description: null }])
        : url === "/kb/{kb_id}/documents" ? ok([failed]) : undefined),
      POST: async (url) => (url === "/documents/{doc_id}/reprocess" ? reprocess() : undefined),
    });
    render(<KbAdmin api={api} />);
    expect(await screen.findByText("解析炸了")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试入库" }));
    expect(reprocess).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑到红**（模块不存在）

- [ ] **Step 3: 实现 `src/components/KbAdmin.tsx`**

```tsx
"use client";
// 知识库后台：kb 列表/新建（无删除端点，不做）→ 选中后文档表轮询 + 上传 + reprocess + chunks 抽屉
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, uploadDocument, type Client } from "@/lib/api";
import { useAsync, usePolling } from "@/lib/hooks";
import type { ChunkOut, DocOut, KbOut } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  pending: "排队中", parsing: "解析中", ready: "就绪", failed: "失败",
};
const terminal = (docs: DocOut[]) => docs.every((d) => d.status === "ready" || d.status === "failed");

export default function KbAdmin({ api }: { api: Client }) {
  const [kbId, setKbId] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<unknown>(null);
  const [chunks, setChunks] = useState<{ doc: DocOut; rows: ChunkOut[] } | null>(null);
  const kbs = useAsync(() => call(api.GET("/kb")) as Promise<KbOut[]>);
  const docs = usePolling(
    () => (kbId === null ? Promise.resolve([] as DocOut[])
      : call(api.GET("/kb/{kb_id}/documents", { params: { path: { kb_id: kbId } } })) as Promise<DocOut[]>),
    { intervalMs: 3000, stopWhen: terminal, enabled: kbId !== null });

  async function createKb() {
    if (!newName.trim()) return;
    try {
      await call(api.POST("/kb", { body: { name: newName } }));
      setNewName("");
      kbs.reload();
    } catch (e) {
      setActionErr(e);
    }
  }

  async function onFile(f: File | undefined) {
    if (!f || kbId === null) return;
    setBusy(true);
    setActionErr(null);
    try {
      await uploadDocument(api, kbId, f);
      docs.reload();
    } catch (e) {
      setActionErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function reprocess(d: DocOut) {
    try {
      await call(api.POST("/documents/{doc_id}/reprocess", { params: { path: { doc_id: d.id } } }));
      docs.reload();
    } catch (e) {
      setActionErr(e);
    }
  }

  async function viewChunks(d: DocOut) {
    try {
      setChunks({ doc: d, rows: (await call(api.GET("/documents/{doc_id}/chunks",
        { params: { path: { doc_id: d.id } } }))) as unknown as ChunkOut[] });
    } catch (e) {
      setActionErr(e);
    }
  }

  return (
    <div className="flex gap-6 p-6">
      <aside className="w-64 shrink-0 space-y-2">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); createKb(); }}>
          <Input aria-label="新知识库名" placeholder="新知识库名" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Button type="submit" disabled={!newName.trim()}>建库</Button>
        </form>
        <ErrorBanner error={kbs.error} />
        <ul className="space-y-1">
          {(kbs.data ?? []).map((k) => (
            <li key={k.id}>
              <button className={`w-full rounded px-2 py-1 text-left text-sm hover:bg-accent ${k.id === kbId ? "bg-accent" : ""}`}
                      onClick={() => { setKbId(k.id); setChunks(null); }}>
                {k.name}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="min-w-0 flex-1">
        {!kbId && <p className="text-sm text-muted-foreground">选择或新建一个知识库</p>}
        {kbId !== null && (
          <>
            <div className="mb-3 flex items-center gap-3">
              <label className="text-sm">
                <input type="file" accept=".txt,.md,.pdf,.docx,.xlsx,.pptx" disabled={busy}
                       onChange={(e) => onFile(e.target.files?.[0])} />
              </label>
              {busy && <Badge variant="secondary">上传中…</Badge>}
            </div>
            <table className="w-full text-sm">
              <thead><tr className="border-b text-left"><th className="py-1">文档</th><th>状态</th><th>大小</th><th /></tr></thead>
              <tbody>
                {(docs.data ?? []).map((d) => (
                  <tr key={d.id} className="border-b">
                    <td className="py-1">{d.name}</td>
                    <td><Badge variant={d.status === "failed" ? "destructive" : "secondary"}>{STATUS_LABEL[d.status] ?? d.status}</Badge>
                      {d.error && <p className="text-xs text-red-600">{d.error}</p>}</td>
                    <td>{d.size_bytes} B</td>
                    <td className="space-x-2 text-right">
                      {d.status === "failed" && <Button size="sm" variant="outline" onClick={() => reprocess(d)}>重试入库</Button>}
                      <Button size="sm" variant="ghost" onClick={() => viewChunks(d)}>看切块</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {docs.loading && !docs.data && <p className="text-sm text-muted-foreground">载入…</p>}
            <ErrorBanner error={docs.error ?? actionErr} />
          </>
        )}
      </section>
      {chunks && (
        <aside className="w-96 shrink-0 space-y-2 overflow-y-auto border-l bg-muted/30 p-4">
          <div className="flex justify-between">
            <h3 className="font-medium">{chunks.doc.name}：{chunks.rows.length} 块</h3>
            <Button size="sm" variant="ghost" onClick={() => setChunks(null)}>关闭</Button>
          </div>
          {chunks.rows.map((c) => (
            <pre key={c.id} className="whitespace-pre-wrap rounded bg-background p-2 text-xs">
              #{c.chunk_index}{c.has_embedding ? "" : "（无向量）"} {"\n"}{c.content.slice(0, 200)}
            </pre>
          ))}
        </aside>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 跑到绿 + 页面挂载** —— `src/app/admin/kb/page.tsx`

```tsx
import KbAdmin from "@/components/KbAdmin";
import { api } from "@/lib/api";

export default function Page() {
  return <KbAdmin api={api} />;
}
```

Run: `cd frontend && npx vitest run && npm run typecheck`

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "stage1 任务7-5: 知识库后台——建库/上传/ARQ 状态 3s 轮询（ready|failed 停）/reprocess/chunks 抽屉"
```

---

### Task 6: `/admin/models` + `/admin/usage`

**Files:**
- Create: `frontend/src/components/ModelsAdmin.tsx`、`frontend/src/components/UsageAdmin.tsx`、`frontend/src/app/admin/models/page.tsx`、`frontend/src/app/admin/usage/page.tsx`
- Test: `frontend/src/components/__tests__/ModelsAdmin.test.tsx`、`frontend/src/components/__tests__/UsageAdmin.test.tsx`

**Interfaces:**
- Consumes: Task 3
- Produces: `<ModelsAdmin api={Client} />`、`<UsageAdmin api={Client} />`；`SCENARIOS = ["chat","embedding","rerank","vision"] as const`（放 `ModelsAdmin.tsx` 导出——与 backend/app/main.py:26 同步，改一处须双改 + 后端 pattern 声明）。

- [ ] **Step 1: 写失败测试**（必锁行为③④⑤）

`ModelsAdmin.test.tsx`：

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ModelsAdmin from "@/components/ModelsAdmin";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { ModelOut } from "@/lib/types";
import { describe, expect, it, vi } from "vitest";

const m: ModelOut = { id: 3, scenario: "chat", provider: "bailian", base_url: "https://x",
  model_name: "qwen3.7-max", capabilities: {}, is_default: true, fallback_rank: 0,
  enabled: true, api_key_masked: "****7f3a" };

it("shows masked key as-is, never reconstructed", async () => {
  const api = fakeApi({ GET: async (url) => (url === "/models" ? ok([m]) : undefined) });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByText("****7f3a")).toBeInTheDocument();
});

it("toggles enabled via PATCH", async () => {
  const patch = vi.fn(async () => ok({ ...m, enabled: false }));
  const api = fakeApi({
    GET: async (url) => (url === "/models" ? ok([m]) : undefined),
    PATCH: async (url, init) => (url === "/models/{model_id}" ? patch(url, init) : undefined),
  });
  render(<ModelsAdmin api={api} />);
  await userEvent.click(await screen.findByRole("switch", { name: "启用 qwen3.7-max" }));
  expect(patch).toHaveBeenCalledWith("/models/{model_id}", expect.objectContaining({
    params: { path: { model_id: 3 } }, body: { enabled: false },
  }));
});

it("validates required fields before POST", async () => {
  const post = vi.fn(async () => ok(m));
  const api = fakeApi({ GET: async () => ok([]), POST: post });
  render(<ModelsAdmin api={api} />);
  await userEvent.click(screen.getByRole("button", { name: "提交登记" }));
  expect(await screen.findByText(/必填/)).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

it("renders 503 detail from backend (gateway secret missing)", async () => {
  const api = fakeApi({ GET: async () => fail("未配置主密钥 GATEWAY_SECRET，无法管理模型 key", 503) });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("GATEWAY_SECRET");
});
```

`UsageAdmin.test.tsx`：

```tsx
import { render, screen } from "@testing-library/react";
import UsageAdmin from "@/components/UsageAdmin";
import { fakeApi, ok } from "@/lib/testkit";

it("aggregates totals and per model rows", async () => {
  const api = fakeApi({ GET: async () => ok([
    { scenario: "chat", model: "qwen3.7-max", calls: 12, prompt_tokens: 100, completion_tokens: 50 },
    { scenario: "embedding", model: "qwen3.7-text-embedding", calls: 30, prompt_tokens: 300, completion_tokens: 0 },
  ]) });
  render(<UsageAdmin api={api} />);
  expect(await screen.findByText("qwen3.7-max")).toBeInTheDocument();
  expect(screen.getByText(/42 次/)).toBeInTheDocument();      // 汇总卡 12+30
  expect(screen.getByText(/450/)).toBeInTheDocument();        // tokens 合计
  expect(screen.queryByText(/qwen3.7-text-rerank/)).not.toBeInTheDocument();
});
```

（修订记录：初稿末行曾留占位断言，已替换为上文的 queryByText 负断言。）

- [ ] **Step 2: 跑到红**

- [ ] **Step 3: 实现 `ModelsAdmin.tsx`**

```tsx
"use client";
// 模型后台：列表（key 掩码原样展示）/登记表单（scenario 下拉+必填校验）/启停/删除
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { ModelOut } from "@/lib/types";

export const SCENARIOS = ["chat", "embedding", "rerank", "vision"] as const;

const emptyForm = { scenario: "chat", provider: "", base_url: "", api_key: "", model_name: "", fallback_rank: 0, enabled: true, is_default: false };

export default function ModelsAdmin({ api }: { api: Client }) {
  const [form, setForm] = useState({ ...emptyForm });
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const list = useAsync(() => call(api.GET("/models")) as Promise<ModelOut[]>);

  async function register() {
    for (const k of ["provider", "base_url", "api_key", "model_name"] as const) {
      if (!String(form[k]).trim()) {
        setFormErr("厂商/地址/key/模型名均为必填");
        return;
      }
    }
    setFormErr(null);
    setBusy(true);
    try {
      await call(api.POST("/models", { body: form }));
      setForm({ ...emptyForm });
      list.reload();
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(m: ModelOut) {
    await call(api.PATCH("/models/{model_id}", { params: { path: { model_id: m.id } }, body: { enabled: !m.enabled } }));
    list.reload();
  }

  async function remove(m: ModelOut) {
    await callVoid(api.DELETE("/models/{model_id}", { params: { path: { model_id: m.id } } }));
    list.reload();
  }

  return (
    <div className="space-y-6 p-6">
      <ErrorBanner error={list.error} />
      <table className="w-full text-sm">
        <thead><tr className="border-b text-left"><th className="py-1">场景</th><th>模型</th><th>API Key</th><th>fallback</th><th>操作</th></tr></thead>
        <tbody>
          {(list.data ?? []).map((m) => (
            <tr key={m.id} className="border-b">
              <td className="py-1"><Badge variant="secondary">{m.scenario}</Badge></td>
              <td>{m.model_name}{m.is_default && <span className="ml-1 text-xs text-blue-600">默认</span>}
                {!m.enabled && <span className="ml-1 text-xs text-muted-foreground">（停用）</span>}</td>
              <td className="font-mono">{m.api_key_masked}</td>
              <td>#{m.fallback_rank}</td>
              <td className="space-x-2">
                <button role="switch" aria-checked={m.enabled} aria-label={`启用 ${m.model_name}`}
                        className="rounded border px-2 text-xs" onClick={() => toggle(m)}>
                  {m.enabled ? "停用" : "启用"}
                </button>
                <Button size="sm" variant="ghost" onClick={() => remove(m)}>删除</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="max-w-lg space-y-2 border-t pt-4" onSubmit={(e) => { e.preventDefault(); register(); }}>
        <h3 className="font-medium">登记模型</h3>
        <select aria-label="scenario" className="w-full rounded border p-2 text-sm"
                value={form.scenario} onChange={(e) => setForm({ ...form, scenario: e.target.value })}>
          {SCENARIOS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {(["provider", "base_url", "api_key", "model_name"] as const).map((k) => (
          <Input key={k} aria-label={k} placeholder={k} value={String(form[k])} type={k === "api_key" ? "password" : "text"}
                 onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
        ))}
        <Input aria-label="fallback_rank" type="number" value={form.fallback_rank}
               onChange={(e) => setForm({ ...form, fallback_rank: Number(e.target.value) || 0 })} />
        {formErr && <p className="text-sm text-red-600">{formErr}</p>}
        <Button type="submit" disabled={busy}>{busy ? "提交中…" : "提交登记"}</Button>
      </form>
    </div>
  );
}
```

（submit 按钮文案即测试锚点"提交登记"；`SCENARIOS` 与 backend/app/main.py:26 的 `SCENARIOS` 集合是手工同步点——后端加场景须同时改这里与 spec pattern。）

- [ ] **Step 4: 实现 `UsageAdmin.tsx`**

```tsx
"use client";
import { Card } from "@/components/ui/card";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, type Client } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { UsageOut } from "@/lib/types";

export default function UsageAdmin({ api }: { api: Client }) {
  const sum = useAsync(() => call(api.GET("/usage/summary")) as Promise<UsageOut[]>);
  const rows = sum.data ?? [];
  const totalCalls = rows.reduce((a, r) => a + r.calls, 0);
  const totalTokens = rows.reduce((a, r) => a + r.prompt_tokens + r.completion_tokens, 0);
  return (
    <div className="space-y-6 p-6">
      <ErrorBanner error={sum.error} />
      <div className="flex gap-4">
        <Card className="flex-1 p-4"><p className="text-sm text-muted-foreground">总调用</p><p className="text-2xl font-semibold">{totalCalls} 次</p></Card>
        <Card className="flex-1 p-4"><p className="text-sm text-muted-foreground">总 tokens</p><p className="text-2xl font-semibold">{totalTokens}</p></Card>
      </div>
      <table className="w-full text-sm">
        <thead><tr className="border-b text-left"><th className="py-1">场景</th><th>模型</th><th>调用</th><th>prompt</th><th>completion</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.scenario}:${r.model}`} className="border-b">
              <td className="py-1">{r.scenario}</td><td>{r.model}</td><td>{r.calls}</td><td>{r.prompt_tokens}</td><td>{r.completion_tokens}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5: 两个 page 挂载 + 全绿 + Commit**

`src/app/admin/models/page.tsx`、`src/app/admin/usage/page.tsx`：同 Task 4 Step 5 模式（import 组件 + `api` 单例；此处必须写出完整文件内容执行）：

```tsx
import ModelsAdmin from "@/components/ModelsAdmin";
import { api } from "@/lib/api";
export default function Page() { return <ModelsAdmin api={api} />; }
```

```tsx
import UsageAdmin from "@/components/UsageAdmin";
import { api } from "@/lib/api";
export default function Page() { return <UsageAdmin api={api} />; }
```

Run: `cd frontend && npx vitest run && npm run typecheck`

```bash
git add frontend/src
git commit -m "stage1 任务7-6: 模型后台（掩码/登记必填/启停 PATCH/503 透传）+ 用量看板"
```

---

### Task 7: 导航收口 + 真机验证 + 文档回归

**Files:**
- Modify: `frontend/src/app/layout.tsx`（导航 + metadata）
- Modify: `README.md`、`产品需求与开发方案.md` §6.5

**Interfaces:** Consumes 全部；Produces 无。

- [ ] **Step 1: layout 导航** —— `src/app/layout.tsx` 整体替换（保留脚手架字体 import）

```tsx
import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "Umax 知识库问答", description: "私有化企业 RAG" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body className="bg-background text-foreground antialiased">
        <nav className="flex h-12 items-center gap-4 border-b px-4 text-sm">
          <span className="font-semibold">Umax RAG</span>
          <Link href="/">聊天</Link>
          <Link href="/admin/kb">知识库</Link>
          <Link href="/admin/models">模型</Link>
          <Link href="/admin/usage">用量</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
```

（ChatApp 的 `h-[calc(100vh-3rem)]` 与此 48px 导航对应。）

- [ ] **Step 2: 前端全量回归**

```bash
cd frontend && npm test && npm run typecheck && npm run build
```
Expected: 全绿、0 类型错误、build 成功（若 ChatApp 那个"turn 锁"细节在 build 的 eslint 阶段报错，按注释授权重构为独立 `sending` state）。

- [ ] **Step 3: 真机冒烟（人工，验收 §6）**

```bash
# 终端 A（PG 容器需在跑）：cd backend && ../.venv/Scripts/python.exe -m app.main
# 终端 B：cd frontend && npm run dev
```
浏览器 http://localhost:3000：建库→上传一个中文 .md→状态轮询到"就绪"→聊天提问看引用抽屉→/admin/models 503 或列表（取决于 .env GATEWAY_SECRET）→/admin/usage 有 chat 计数。**结果写进 commit message 或报告，不许默默跳过。**

- [ ] **Step 4: 文档更新**

README「当前进度」+ 需求文档 §6.5 追加任务 7 完成条目（前端启动方式、rewrite 代理、契约消费方式、测试命令）；requirements.txt 无变化则不动。

- [ ] **Step 5: 后端回归确认 + Commit**

```bash
cd backend && ../.venv/Scripts/python.exe -m pytest          # 93 passed + 17 子测试
cd .. && git status
git add frontend/src/app/layout.tsx README.md 产品需求与开发方案.md
git commit -m "stage1 任务7-7: 前端导航收口 + 真机冒烟 + README/需求文档任务7记录"
```

---

## 计划自查记录（writing-plans self-review）

1. **Spec 覆盖**：§1 工程链路→T2；§2 四页→T4/T5/T6；§3 数据层→T3；§4 测试五必锁→T3(errText/upload)、T4(引用)、T5(轮询)、T6(掩码+必填+503)；§5 边界已体现（无 kb 删除、无流式）；§6 验收→T7 Step3。契约缺口（文档列表）→T1。✔
2. **占位符**：UsageAdmin 测试尾行、ModelsAdmin 按钮文案两处已内联标注修订方式（保留哪个值以测试断言语义为准），无 TBD。✔
3. **类型一致性**：`Client`/`call`/`uploadDocument`/`usePolling` 签名 T3 定义、T4-6 消费一致；`DocOut.status` 四态与 backend 状态机一致；DTO 字段与 `_doc_json`/`_model_json`/chat/usage 逐一对过。✔
