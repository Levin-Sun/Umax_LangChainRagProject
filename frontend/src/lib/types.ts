export type DocStatus = "pending" | "parsing" | "ready" | "failed";
export interface KbOut { id: number; name: string; description: string | null }
export interface DocOut { id: number; kb_id: number; name: string; status: DocStatus; error: string | null; size_bytes: number }
export interface ChunkOut { id: number; chunk_index: number; content: string; has_embedding: boolean }
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
export interface SettingsSnapshot { values: Record<string, string | number>; defaults: Record<string, string | number>; overridden: string[]; warnings: string[]; labels: Record<string, string>; help: Record<string, string> }
export interface AuditOut { id: number; user_email: string; action: string; target_type: string | null; target_id: number | null; detail: Record<string, unknown>; ip: string | null; created_at: string }
