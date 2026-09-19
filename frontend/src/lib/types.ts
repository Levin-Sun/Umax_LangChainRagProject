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
