import { createApiClient } from "@umax/sdk-ts";
import { P } from "./paths";
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
  return (await call(client.POST(P.kbDocs,
    { params: { path: { kb_id: kbId } }, body: form as never }))) as unknown as DocOut;
}
