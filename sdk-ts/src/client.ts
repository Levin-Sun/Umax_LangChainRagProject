// sdk-ts/src/client.ts
// 契约驱动生成：schema.d.ts 来自 openapi.json（npm run gen），手写只有这个薄封装
import createClient, { type ClientOptions } from "openapi-fetch";
import type { paths } from "./schema.js";

// baseUrl 默认为空：路径键自带 /api/v1 前缀（唯一事实源），浏览器相对同源路径经 Next rewrite 代理
export function createApiClient(baseUrl = "", options?: Partial<ClientOptions>) {
  return createClient<paths>({ baseUrl, ...options });
}
export type { paths };
