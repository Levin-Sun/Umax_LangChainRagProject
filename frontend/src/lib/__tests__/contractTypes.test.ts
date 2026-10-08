// 契约类型耦合断言（编译期，无运行时）。
//
// 为什么需要它：响应模型补齐之前，前端只能手写类型 + `as Promise<KbOut[]>` 强转，
// 后端改一个字段名（size_bytes → bytes）不会有任何测试变红，只会在浏览器里静默变成 undefined。
// 现在后端每个"会返回正文"的端点都声明了 response_model，SDK 类型由 spec 生成——
// 这里把**手写类型与生成类型做双向可赋值断言**：两边必须等价，任一侧漂移都在 `npm run typecheck` 变红。
//
// Equal<A, B> 的方向：只有 A ⊆ B 且 B ⊆ A 才为 true，等价于"两个类型完全一致"。
// 断言写成 `const _x: Equal<...> = true`：不等价时 TS 直接报错，且错误信息里能看到是哪一对。
import { describe, expect, it } from "vitest";
import type { paths } from "@umax/sdk-ts";
import type {
  ApiKeyCreated, ApiKeyOut, AuditOut, AuthMe, BatchUploadItem, BrandingOut, ChatOut,
  ChunkOut, ConversationOut, DocOut, EvalQuestionOut, EvalRunDetailOut, EvalRunOut, JobOut,
  KbOut, LicenseOut, MessageOut, ModelOut, QuotaOut, ReindexOut, SettingsSnapshot, UsageOut,
  UsageUserOut, UserOut,
} from "@/lib/types";

/** 两个类型完全一致才为 true（双向可赋值）；不等价时 `= true` 处直接编译报错。 */
type Equal<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;

/** 某个操作的 200/201 响应体类型（直接索引生成类型，不做任何包装）。 */
type R<P extends keyof paths, M extends keyof paths[P]> =
  paths[P][M] extends { responses: { 200: { content: { "application/json": infer J } } } }
    ? J : never;
type R201<P extends keyof paths, M extends keyof paths[P]> =
  paths[P][M] extends { responses: { 201: { content: { "application/json": infer J } } } }
    ? J : never;
type R202<P extends keyof paths, M extends keyof paths[P]> =
  paths[P][M] extends { responses: { 202: { content: { "application/json": infer J } } } }
    ? J : never;

// ---- 逐端点断言：后端换字段名 → 这里编译失败 ----
const _kb: Equal<KbOut[], R<"/api/v1/kb", "get">> = true;
const _doc: Equal<DocOut, R<"/api/v1/documents/{doc_id}", "get">> = true;
const _chunk: Equal<ChunkOut[], R<"/api/v1/documents/{doc_id}/chunks", "get">> = true;
const _batch: Equal<BatchUploadItem[], R201<"/api/v1/kb/{kb_id}/documents/batch", "post">> = true;
const _chat: Equal<ChatOut, R<"/api/v1/chat", "post">> = true;
const _convs: Equal<ConversationOut[], R<"/api/v1/conversations", "get">> = true;
const _msgs: Equal<MessageOut[], R<"/api/v1/conversations/{conv_id}/messages", "get">> = true;
const _models: Equal<ModelOut[], R<"/api/v1/models", "get">> = true;
const _users: Equal<UserOut[], R<"/api/v1/users", "get">> = true;
const _audit: Equal<AuditOut[], R<"/api/v1/audit", "get">> = true;
const _quota: Equal<QuotaOut, R<"/api/v1/usage/me", "get">> = true;
const _usageUsers: Equal<UsageUserOut[], R<"/api/v1/usage/users", "get">> = true;
const _usage: Equal<UsageOut[], R<"/api/v1/usage/summary", "get">> = true;
const _keys: Equal<ApiKeyOut[], R<"/api/v1/api-keys", "get">> = true;
const _keyCreated: Equal<ApiKeyCreated, R201<"/api/v1/api-keys", "post">> = true;
const _branding: Equal<BrandingOut, R<"/api/v1/branding", "get">> = true;
const _license: Equal<LicenseOut, R<"/api/v1/license", "get">> = true;
const _settings: Equal<SettingsSnapshot, R<"/api/v1/settings", "get">> = true;
const _me: Equal<AuthMe, R<"/api/v1/auth/me", "get">> = true;
const _questions: Equal<EvalQuestionOut[], R<"/api/v1/eval/questions", "get">> = true;
const _runs: Equal<EvalRunOut[], R<"/api/v1/eval/runs", "get">> = true;
const _runDetail: Equal<EvalRunDetailOut, R<"/api/v1/eval/runs/{run_id}", "get">> = true;

// 编译期断言本身没有运行时要断的东西；这条用例的作用是让 vitest 也跑到这个文件
// （否则一个只含类型断言的文件不会被 `npm test` 收集，只有 typecheck 会看它）。
describe("契约类型耦合", () => {
  it("手写类型与生成的 SDK 类型等价（任一漂移都会在 typecheck 变红）", () => {
    const asserted = [_kb, _doc, _chunk, _batch, _chat, _convs, _msgs, _models, _users,
                      _audit, _quota, _usageUsers, _usage, _keys, _keyCreated, _branding,
                      _license, _settings, _me, _questions, _runs, _runDetail, _jobs, _reindex];
    expect(asserted.every((x) => x === true)).toBe(true);
  });
});

// 评审遗留：后台作业（含重建索引的作业号）也纳入耦合断言——新增接口最容易忘了同步类型
const _jobs: Equal<JobOut[], R<"/api/v1/jobs", "get">> = true;
const _reindex: Equal<ReindexOut, R202<"/api/v1/reindex", "post">> = true;
