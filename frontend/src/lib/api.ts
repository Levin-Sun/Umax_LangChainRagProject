import { createApiClient } from "@umax/sdk-ts";
import { P } from "./paths";
import type { DocOut } from "./types";

export type Client = Pick<ReturnType<typeof createApiClient>, "GET" | "POST" | "PATCH" | "DELETE">;
export const api: Client = createApiClient();

export class ApiError extends Error {
  constructor(public body: unknown, public status?: number) {
    super(errText(body));
  }
}

export function is401(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

export function errText(body: unknown): string {
  // call() 抛出的是 ApiError（原始响应体在 .body）——useAsync 存的是抛出的 error，
  // 组件把它直接喂给 ErrorBanner；此处统一拆包，任务 4-6 共享（任务 4 实测：
  // 不拆则 errText 读不到 .detail，横幅只剩兜底文案）。
  if (body instanceof ApiError) return errText(body.body);
  if (typeof body === "string") return body;
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "请求不合法（字段校验失败）";
  return "网络或服务异常，请稍后重试";
}

export async function call<T>(p: Promise<{ data?: T; error?: unknown; response?: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (error !== undefined) throw new ApiError(error, response?.status);
  if (data === undefined) throw new ApiError(undefined, response?.status);
  return data;
}

export async function callVoid(p: Promise<{ data?: unknown; error?: unknown; response?: Response }>): Promise<void> {
  const { error, response } = await p;
  if (error !== undefined) throw new ApiError(error, response?.status);
}

// 管理会话退出：后端清 cookie 后执行跳转钩子（after 注入以便测试，Nav 传 location 跳转）
export async function logout(client: Client, after: () => void): Promise<void> {
  await callVoid(client.POST(P.adminLogout, {} as never));
  after();
}

// 后端 login 同时下发的 JS 可读标记（授权仍只认 HttpOnly 的 admin_session）；
// 伪造它最多看到空壳管理页 + 401 横幅，无安全影响
export function isAdminHint(): boolean {
  return typeof document !== "undefined" && /(?:^|; )admin_hint=1/.test(document.cookie);
}

export async function uploadDocument(client: Client, kbId: number, file: File): Promise<DocOut> {
  const form = new FormData();
  form.append("file", file, file.name);
  // 后端 200 未建 response_model（spec 里是 unknown），DTO 断言集中在此
  return (await call(client.POST(P.kbDocs,
    { params: { path: { kb_id: kbId } }, body: form as never }))) as unknown as DocOut;
}
