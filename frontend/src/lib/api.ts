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

// 401 统一出口（会话过期/未登录被后端踢）：注入跳转钩子，默认 no-op——
// Providers 挂载时注 `() => location.assign("/admin/login")`，测试环境保持 no-op。
let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void): void { onUnauthorized = fn; }

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
  if (error !== undefined) { if (response?.status === 401) onUnauthorized(); throw new ApiError(error, response?.status); }
  if (data === undefined) { if (response?.status === 401) onUnauthorized(); throw new ApiError(undefined, response?.status); }
  return data;
}

export async function callVoid(p: Promise<{ data?: unknown; error?: unknown; response?: Response }>): Promise<void> {
  const { error, response } = await p;
  if (error !== undefined) { if (response?.status === 401) onUnauthorized(); throw new ApiError(error, response?.status); }
}

export async function uploadDocument(client: Client, kbId: number, file: File): Promise<DocOut> {
  const form = new FormData();
  form.append("file", file, file.name);
  // 后端 200 未建 response_model（spec 里是 unknown），DTO 断言集中在此
  return (await call(client.POST(P.kbDocs,
    { params: { path: { kb_id: kbId } }, body: form as never }))) as unknown as DocOut;
}
