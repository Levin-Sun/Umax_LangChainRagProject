export type DocStatus = "pending" | "parsing" | "ready" | "failed";
export interface KbOut { id: number; name: string; description: string | null }
export interface DocOut { id: number; kb_id: number; name: string; status: DocStatus; error: string | null; size_bytes: number }
export interface ChunkOut { id: number; chunk_index: number; content: string; has_embedding: boolean }
export interface Citation { n: number; doc_name: string; chunk_id: number; excerpt: string }
export interface ChatOut { conversation_id: number; answer: string; citations: Citation[]; cited_docs: string[]; usage: { prompt_tokens: number; completion_tokens: number } }
export interface ConversationOut { id: number; title: string; kb_ids: number[] }
export interface MessageOut { id: number; role: "user" | "assistant"; content: { type: string; text?: string }[]; citations: Citation[] | null }
export interface ModelOut { id: number; scenario: string; provider: string; base_url: string; model_name: string; capabilities: Record<string, unknown>; is_default: boolean; fallback_rank: number; enabled: boolean; api_key_masked: string }
export interface UsageOut { scenario: string; model: string; calls: number; prompt_tokens: number; completion_tokens: number }
// 登录态唯一真相源（GET /auth/me）：kb_ids null=admin 隐式全库，数组=member 库级授权
export interface AuthMe { email: string; name: string; role: "admin" | "member"; kb_ids: number[] | null }
export interface UserOut { id: number; email: string; name: string; role: string; status: string; created_at: string; kb_ids: number[] | null }
export interface AuditOut { id: number; user_email: string; action: string; target_type: string | null; target_id: number | null; detail: Record<string, unknown>; ip: string | null; created_at: string }
