// sdk-ts/src/client.ts
// 契约驱动生成：schema.d.ts 来自 openapi.json（npm run gen），手写只有这个薄封装
import createClient, { type ClientOptions } from "openapi-fetch";
import type { paths } from "./schema.js";

export function createApiClient(baseUrl = "/api/v1", options?: Partial<ClientOptions>) {
  return createClient<paths>({ baseUrl, ...options });
}
export type { paths };
