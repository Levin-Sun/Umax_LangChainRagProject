// @vitest-environment node
// Ruling-realm：vitest jsdom 环境下 FormData/File 被 jsdom 覆盖、Request 为 Node undici 实现，
// 跨 realm 品牌校验失败导致 body 被字符串化（multipart 断言必红）——本用例隔离到 node realm，
// Request/FormData/File/Response 全为 Node 原生同 realm，断言语义零改动。
import { createApiClient } from "@umax/sdk-ts";
import { describe, expect, it } from "vitest";
import { uploadDocument, type Client } from "@/lib/api";

describe("uploadDocument", () => {
  // openapi-fetch 0.17 实调 fetch(request, requestInitExt)（dist/index.mjs:123），
  // FormData 体不手设 Content-Type（:60-63，boundary 由 Request 生成）——断言捕获第一参 Request。
  it("sends real FormData (multipart), not JSON", async () => {
    let captured: Request | undefined;
    const fakeFetch = (async (req: Request) => {
      captured = req;
      return new Response(JSON.stringify({ id: 7, kb_id: 1, name: "a.txt",
        status: "pending", error: null, size_bytes: 3 }),
        { status: 201, headers: { "content-type": "application/json" } });
    }) as unknown as typeof fetch;
    const client = createApiClient("http://test.local", { fetch: fakeFetch }) as Client;
    const doc = await uploadDocument(client, 1, new File(["abc"], "a.txt", { type: "text/plain" }));
    expect(captured!.url).toBe("http://test.local/api/v1/kb/1/documents");
    const form = await captured!.formData();
    expect(form.get("file")).toBeInstanceOf(File);
    expect(doc.id).toBe(7);
  });
});
