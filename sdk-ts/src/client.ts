// sdk-ts/src/client.ts
// 契约驱动生成：schema.d.ts 来自 openapi.json（npm run gen），手写只有这个薄封装
import createClient from "openapi-fetch";
import type { paths } from "./schema.js";

export function createApiClient(baseUrl = "/api/v1") {
  return createClient<paths>({ baseUrl });
}
export type { paths };
