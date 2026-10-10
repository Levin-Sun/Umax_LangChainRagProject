export type DocStatus = "pending" | "parsing" | "ready" | "failed";
export interface KbOut { id: number; name: string; description: string | null }
// has_embedding：这篇是否有向量（≥1 个切块带向量）。没有向量＝当时没有可用的向量模型，
// 只能按字面搜——界面据此提示"去配向量模型 / 重建索引"（先传文档后配模型是会真实发生的顺序）。
export interface DocOut { id: number; kb_id: number; name: string; status: DocStatus; error: string | null; size_bytes: number | null; created_at: string; has_embedding: boolean }
// 批量上传逐项结果（部分成功：坏文件只在自己这行有 error）
export interface BatchUploadItem { name: string; document: DocOut | null; error: string | null }
// 重建索引（换 embedding 模型后全库重算）：documents=本次要重建的文档数，kb_ids null=全部知识库。
// job_id=作业号：进程被杀后靠它查"跑完没有"（作业记录是评审补的那个盲区）
export interface ReindexOut { documents: number; kb_ids: number[] | null; job_id: number }
// 后台作业（/jobs）：作业回答"谁为什么发起、最后成不成"，每一篇跑到哪了看文档列表——
// 两套进度必然漂移，所以只留一套真进度
export type JobStatus = "queued" | "running" | "done" | "failed" | "interrupted";
export interface JobOut { id: number; kind: string; status: JobStatus; scope: { kb_ids: number[] | null }; total: number; done: number; failed: number; error: string | null; created_by: string | null; started_at: string; finished_at: string | null }
export interface ChunkOut { id: number; chunk_index: number; content: string; has_embedding: boolean; meta: Record<string, unknown> }
export interface Citation { n: number; doc_name: string; chunk_id: number; excerpt: string }
// degraded_reason=null 表示正常由模型作答；no_hit/no_model/model_error 表示走了兜底话术（三者文案相同，必须靠它区分）
export interface ChatOut { conversation_id: number; answer: string; citations: Citation[]; cited_docs: string[]; usage: { prompt_tokens: number; completion_tokens: number }; degraded_reason?: string | null }
export interface ConversationOut { id: number; title: string; kb_ids: number[] }
// 消息片段：与契约 MessagePartOut 逐字对齐（契约把它声明成"显式字段 + extra 放行"的容器）。
// 索引签名保留未来新增的片段类型（文件、音频）——契约侧也是这么放行的。
export interface MessagePart { type: string; text?: string | null; image_url?: { url: string } | null; [key: string]: unknown }
export interface MessageOut { id: number; role: "user" | "assistant" | "system"; content: MessagePart[]; citations: Citation[] | null }
export interface ModelOut { id: number; scenario: "chat" | "embedding" | "rerank" | "vision"; provider: string; base_url: string; model_name: string; capabilities: Record<string, unknown>; is_default: boolean; fallback_rank: number; enabled: boolean; api_key_masked: string }
// 内置厂商标本（GET /model-catalog）：界面据此做「选厂商 → 一键配齐」，客户不必知道
// 四类模型各自的端点与模型名。capabilities 里的 key 是"能力"（用途），scenario 是系统内部路由键。
export interface CatalogProfile { id: string; name: string; base_url: string; hint?: string | null }
export interface CatalogCapability { key: string; scenario: string; model: string; profile: string; dim?: number | null; note?: string | null }
export interface CatalogVendor { id: string; name: string; aliases: string[]; pinyin: string[]; verified: boolean; verified_at?: string | null; profiles: CatalogProfile[]; capabilities: CatalogCapability[]; note?: string | null }
export interface ModelCatalog { catalog_version: string; updated_at: string; capability_labels: Record<string, string>; capability_miss: Record<string, string>; vendors: CatalogVendor[] }
// 一键配齐的结果：action=created/updated（幂等，反复点不会堆重复）；ok=试调结果（null=未试调）
export interface BundleItem { capability: string; scenario: string; model: string; base_url: string; action: string; ok: boolean | null; detail: string | null }
export interface BundleOut { vendor: string; items: BundleItem[] }
// avg_latency_ms：该分组平均耗时（毫秒；全为 NULL 时 null）。token 看成本，耗时看体验。
export interface UsageOut { scenario: string; model: string; calls: number; prompt_tokens: number; completion_tokens: number; avg_latency_ms: number | null }
// 登录态唯一真相源（GET /auth/me）：kb_ids null=admin 隐式全库，数组=member 库级授权；
// must_change_password=true 时全站被 428 门闸拦下，前端弹强改密框（首登/管理员重置口令后）
export interface AuthMe { email: string; name: string; role: "admin" | "member"; kb_ids: number[] | null; must_change_password: boolean }
// 配额状态（GET /usage/me 与 /usage/users 同型）：NULL 限额=不限
export interface QuotaOut { daily_used: number; daily_limit: number | null; monthly_used: number; monthly_limit: number | null; near_limit: boolean; exceeded: boolean; warn_ratio: number }
export interface UsageUserOut extends QuotaOut { id: number; email: string; name: string }
export interface UserOut { id: number; email: string; name: string; role: "admin" | "member"; status: "active" | "disabled"; created_at: string; kb_ids: number[] | null; daily_token_limit: number | null; monthly_token_limit: number | null }
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
export interface AuditOut { id: number; user_email: string | null; action: string; target_type: string | null; target_id: number | null; detail: Record<string, unknown>; ip: string | null; created_at: string }
// 评测（/admin/eval）：金标准题 + 一次运行的分数/明细/报告。
// metrics/checks 在后端是 JSONB，但形状已写进 schema（EvalMetricsOut/EvalChecksOut），
// 所以这里按真类型声明而不是 Record<string, unknown>——"指针随便点"的错在编译期就没了。
// 字段全部非可选：契约里它们都带默认值（运行中的 run 由响应模型补成全 0）。
export type EvalRunStatus = "running" | "done" | "failed";
export interface EvalQuestionOut { id: number; question: string; expect_all: string[]; expect_any: string[]; cites: string[]; category: string; note: string | null; enabled: boolean; created_at: string }
export interface EvalCategoryScore { category: string; total: number; passed: number }
export interface EvalMetrics { total: number; passed: number; pass_rate: number; with_cites: number; hit: number; hit_rate: number; mrr: number; avg_latency_ms: number; categories: EvalCategoryScore[]; judge?: EvalJudgeStats }
// retrieval=null：该题没设金标准文档（不适用，不是失败）
export interface EvalChecks { kw_all: boolean; kw_any: boolean; citation: boolean; retrieval?: boolean | null; passed: boolean; rank: number }
// 裁判（LLM 裁判，Ragas 侧）：软指标，永不并入通过率——裁判模型会漂移，硬指标必须可复算。
// judged=判过的题数，scored=判词能解析的题数；faithful/relevance 为 null=判词没解析出来（不是 0 分）
export interface EvalJudgeStats { judged: number; scored: number; faithful: number; relevance: number; faithful_rate: number; relevance_rate: number; model?: string | null }
export interface EvalJudgeVerdict { faithful?: number | null; relevance?: number | null; reason: string; raw?: string | null; model?: string | null }
export interface EvalItemOut { id: number; question_id: number | null; question: string; category: string; note: string | null; expect_all: string[]; expect_any: string[]; cites: string[]; answer: string | null; cited_docs: string[]; top_docs: string[]; checks: EvalChecks; judge?: EvalJudgeVerdict | null; passed: boolean; rank: number; latency_ms: number | null; error: string | null }
export interface EvalRunOut { id: number; status: EvalRunStatus; total: number; passed: number; metrics: EvalMetrics; kb_ids: number[] | null; judge: boolean; chat_model: string | null; embedding_model: string | null; error: string | null; created_by: string | null; started_at: string; finished_at: string | null }
export interface EvalRunDetailOut extends EvalRunOut { items: EvalItemOut[] }
