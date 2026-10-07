export type DocStatus = "pending" | "parsing" | "ready" | "failed";
export interface KbOut { id: number; name: string; description: string | null }
export interface DocOut { id: number; kb_id: number; name: string; status: DocStatus; error: string | null; size_bytes: number | null; created_at: string }
// 批量上传逐项结果（部分成功：坏文件只在自己这行有 error）
export interface BatchUploadItem { name: string; document: DocOut | null; error: string | null }
export interface ChunkOut { id: number; chunk_index: number; content: string; has_embedding: boolean; meta: Record<string, unknown> }
export interface Citation { n: number; doc_name: string; chunk_id: number; excerpt: string }
export interface ChatOut { conversation_id: number; answer: string; citations: Citation[]; cited_docs: string[]; usage: { prompt_tokens: number; completion_tokens: number } }
export interface ConversationOut { id: number; title: string; kb_ids: number[] }
export interface MessageOut { id: number; role: "user" | "assistant"; content: { type: string; text?: string; image_url?: { url: string } }[]; citations: Citation[] | null }
export interface ModelOut { id: number; scenario: string; provider: string; base_url: string; model_name: string; capabilities: Record<string, unknown>; is_default: boolean; fallback_rank: number; enabled: boolean; api_key_masked: string }
export interface UsageOut { scenario: string; model: string; calls: number; prompt_tokens: number; completion_tokens: number }
// 登录态唯一真相源（GET /auth/me）：kb_ids null=admin 隐式全库，数组=member 库级授权；
// must_change_password=true 时全站被 428 门闸拦下，前端弹强改密框（首登/管理员重置口令后）
export interface AuthMe { email: string; name: string; role: "admin" | "member"; kb_ids: number[] | null; must_change_password: boolean }
// 配额状态（GET /usage/me 与 /usage/users 同型）：NULL 限额=不限
export interface QuotaOut { daily_used: number; daily_limit: number | null; monthly_used: number; monthly_limit: number | null; near_limit: boolean; exceeded: boolean; warn_ratio: number }
export interface UsageUserOut extends QuotaOut { id: number; email: string; name: string }
export interface UserOut { id: number; email: string; name: string; role: string; status: string; created_at: string; kb_ids: number[] | null; daily_token_limit: number | null; monthly_token_limit: number | null }
// 开放 API key（列表形态）：明文 key 只在创建响应出现一次，列表只有打码前缀
export interface ApiKeyOut { id: number; name: string; key_prefix: string; kb_ids: number[] | null; monthly_token_quota: number | null; enabled: boolean; last_used_at: string | null; created_at: string }
// 创建响应 = 列表形态 + 一次性明文 key（此后任何接口都不再返回明文）
export type ApiKeyCreated = ApiKeyOut & { key: string }
// 白标（/branding）：GET 匿名可读（登录页首屏），logo 是 data:image base64 或 null
export interface BrandingOut { brand_name: string; logo: string | null }
// 授权状态（/license，admin 面）：enforced=false 表示未启用校验（开发模式）
export interface LicenseOut { enforced: boolean; valid: boolean; reason: string | null; license_key: string | null; customer: string | null; issued_at: string | null; expires_at: string | null; days_left: number | null; features: Record<string, unknown>; machine_fingerprint: string }
// 配置中心（/settings）：values=生效值、defaults=默认值、overridden=被后台改过的键
export interface SettingsSnapshot { values: Record<string, string | number | boolean>; defaults: Record<string, string | number | boolean>; overridden: string[]; warnings: string[]; labels: Record<string, string>; help: Record<string, string> }
export interface AuditOut { id: number; user_email: string; action: string; target_type: string | null; target_id: number | null; detail: Record<string, unknown>; ip: string | null; created_at: string }
// 评测（/admin/eval）：金标准题 + 一次运行的分数/明细/报告。
// metrics/checks 在后端是 JSONB，但形状已写进 schema（EvalMetricsOut/EvalChecksOut），
// 所以这里按真类型声明而不是 Record<string, unknown>——"指针随便点"的错在编译期就没了。
// 字段全部非可选：契约里它们都带默认值（运行中的 run 由响应模型补成全 0）。
export type EvalRunStatus = "running" | "done" | "failed";
export interface EvalQuestionOut { id: number; question: string; expect_all: string[]; expect_any: string[]; cites: string[]; category: string; note: string | null; enabled: boolean; created_at: string }
export interface EvalCategoryScore { category: string; total: number; passed: number }
export interface EvalMetrics { total: number; passed: number; pass_rate: number; with_cites: number; hit: number; hit_rate: number; mrr: number; avg_latency_ms: number; categories: EvalCategoryScore[] }
// retrieval=null：该题没设金标准文档（不适用，不是失败）
export interface EvalChecks { kw_all: boolean; kw_any: boolean; citation: boolean; retrieval: boolean | null; passed: boolean; rank: number }
export interface EvalItemOut { id: number; question_id: number | null; question: string; category: string; note: string | null; expect_all: string[]; expect_any: string[]; cites: string[]; answer: string | null; cited_docs: string[]; top_docs: string[]; checks: EvalChecks; passed: boolean; rank: number; latency_ms: number | null; error: string | null }
export interface EvalRunOut { id: number; status: EvalRunStatus; total: number; passed: number; metrics: EvalMetrics; kb_ids: number[] | null; chat_model: string | null; embedding_model: string | null; error: string | null; created_by: string | null; started_at: string; finished_at: string | null }
export interface EvalRunDetailOut extends EvalRunOut { items: EvalItemOut[] }
