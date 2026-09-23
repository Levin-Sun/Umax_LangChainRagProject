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
    PUT: ((u: string, i?: unknown) => stubs.PUT?.(u, i) ?? missing(u, "PUT")),
    POST: ((u: string, i?: unknown) => stubs.POST?.(u, i) ?? missing(u, "POST")),
    PATCH: ((u: string, i?: unknown) => stubs.PATCH?.(u, i) ?? missing(u, "PATCH")),
    DELETE: ((u: string, i?: unknown) => stubs.DELETE?.(u, i) ?? missing(u, "DELETE")),
  } as unknown as Client;
}
