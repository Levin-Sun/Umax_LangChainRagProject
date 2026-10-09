# 契约面：请求体/响应模型 + 类型标注 + 错误响应声明（原 main.py 上半部分，拆 router 时整体搬来）。
#
# 为什么单独一个模块：这里产出的**全部**内容都会进 contracts/openapi.json，也就是前后端
# 唯一的事实源。它与"端点怎么实现"是两件事——混在一个文件里时，改一个字段要在一千多行
# 里找位置，且无法一眼看出"契约面到底有哪些东西"。
#
# 约定：这里的字段必须与端点真正返回的键**逐字对齐**。pydantic 会**默默丢掉**模型里没声明
# 的字段（评测 run 的 judge 字段就这样消失过一次），所以改动后要用 /tmp/snapshot_responses.py
# 做字段快照 diff。
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, StrictBool,
                      create_model as pydantic_create_model)

from app.api.constants import EMAIL_PATTERN, IMAGE_PATTERN
from app.services.settings import Spec
from app.services.text import clean_text

SCENARIOS = {"chat", "embedding", "rerank", "vision"}
# scenario 约束的单一事实源：SCENARIOS 派生（sorted 保序：chat|embedding|rerank|vision，
# 与已入库 contracts/openapi.json 逐字一致，改集合必须走契约工作流重导 spec）。
# 终审收口 I3：原为手写正则字面量，与运行时校验集合存在漂移风险。
SCENARIO_PATTERN = "^(" + "|".join(sorted(SCENARIOS)) + ")$"
# 文档解析状态机（模型注释同源：pending → parsing → ready / failed）——单一事实源派生 Literal，
# 进 spec 成 enum：后端拒绝未知状态、前端拿到字面量联合类型（此前 spec 里是裸 string，两端靠约定）
DOC_STATUSES = ("pending", "parsing", "ready", "failed")
# 评测运行状态机（同款派生）：running 是"后台正在跑"，前端据此轮询进度
EVAL_RUN_STATUSES = ("running", "done", "failed")
# 后台作业状态：queued=已交外部 worker / running=同步档线程在跑 / done / failed / interrupted
JOB_STATUSES = ("queued", "running", "done", "failed", "interrupted")

# role/status 枚举同款派生（任务 4）：约束写进 schema，运行时校验集合是同一事实源，防漂移
ROLES = {"admin", "member"}
USER_STATUSES = {"active", "disabled"}
# 消息角色（多模态会话）：user/assistant/system 三个，同样是响应侧枚举的单一事实源
MESSAGE_ROLES = ("user", "assistant", "system")
ROLE_PATTERN = "^(" + "|".join(sorted(ROLES)) + ")$"
USER_STATUS_PATTERN = "^(" + "|".join(sorted(USER_STATUSES)) + ")$"


# 契约 fuzz 修复①：id 落 PG INTEGER（int32）——裸 integer 无界，fuzz 发 2^31 直接 SQL 溢出 500，
# 把 int32 边界写进契约。修复②：body 侧 Strict* 禁 bool/str 混入（lax 强转被 fuzz 判"违法请求被接受"）
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


def _json_int(v):
    # 按 JSON Schema 的 integer 语义对齐契约（fuzz 修复⑥）：整值浮点（-74.0）合法必须收，
    # bool/str/None/dict 等非法要拒——pydantic strict 过紧（拒 -74.0），lax 过松（收 True/"5"）
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("不是 JSON integer")
    if isinstance(v, float):
        if not v.is_integer():
            raise ValueError("不是 JSON integer")
        return int(v)
    return v


def _json_int32(v):
    # fuzz 修复⑦：Field(ge/le) 叠在 BeforeValidator 外层时 pydantic 吐非法 JSON Schema 键
    # "ge"/"le"（裸 Field 的 PathId 则正确吐 minimum/maximum）——契约校验器忽略未知键，
    # 越界值被判合法请求，运行时 422 即"拒绝合法请求"违约。改由验证器统一卡 int32 边界，
    # schema 用 json_schema_extra 显式写标准 minimum/maximum，两侧严格一致。
    v = _json_int(v)
    if not (INT32_MIN <= v <= INT32_MAX):
        raise ValueError("超出 int32（PG INTEGER）范围")
    return v


JsonInt = Annotated[int, BeforeValidator(_json_int)]
PathId = Annotated[int, Field(ge=INT32_MIN, le=INT32_MAX)]     # 路径 id：字符串→int 保持 lax，只卡 int32
ReqId = Annotated[int, BeforeValidator(_json_int32),           # body id：JSON integer 且卡 int32，
            Field(json_schema_extra={"minimum": INT32_MIN, "maximum": INT32_MAX})]  # schema 侧标准键


def _json_nonneg(v):
    # 用户配额专用：JSON integer 且 ≥0（负数不是"超级配额"而是非法输入）——边界写进 schema
    v = _json_int32(v)
    if v < 0:
        raise ValueError("不能为负数")
    return v


NonNegInt = Annotated[int, BeforeValidator(_json_nonneg),
                      Field(json_schema_extra={"minimum": 0, "maximum": INT32_MAX})]


def _query_int(v):
    # 查询参数专用整数口径（任务 5 /audit 分页）：HTTP query 到 FastAPI 手上永远是字符串，
    # 直接套 JsonInt 会把 limit=1 的 "1" 判成"不是 JSON integer"→422（分页端点不可用）；
    # 数字串按值取整，垃圾串/bool/浮点串仍拒（422 由 FastAPI 默认声明入约）。
    # 上下界不在这里卡——端点内钳位（limit 1..200 / offset ≥0），越界不是违法请求。
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            raise ValueError("查询参数不是整数") from None
    return _json_int(v)


QueryInt = Annotated[int, BeforeValidator(_query_int)]        # query 侧整数（limit/offset）


# 规范化闸的实现在 services/text（请求侧与模型输出侧共用同一份规则）
_clean_text = clean_text


def _utf8_str(v):
    return _clean_text(v)


Utf8Str = Annotated[str, BeforeValidator(_utf8_str)]           # 落库字符串统一过 NUL/代理规范化闸
JsonSafe = Annotated[dict, BeforeValidator(_utf8_str)]         # JSONB 列（capabilities）递归过闸


DocStatus = Literal[*DOC_STATUSES]   # 3.11+ 解包写法：与 DOC_STATUSES 不会漂移
EvalRunStatus = Literal[*EVAL_RUN_STATUSES]
JobStatusName = Literal[*JOB_STATUSES]
# 响应侧枚举（评审补齐）：契约要能自己说清 role 只有两个值——此前是裸 string，
# 前端手写联合类型只能算"口头约定"，而且两边都不报错（耦合断言一上来就抓到了这类漂移）
RoleName = Literal[*sorted(ROLES)]
UserStatusName = Literal[*sorted(USER_STATUSES)]
ScenarioName = Literal[*sorted(SCENARIOS)]
MessageRoleName = Literal[*MESSAGE_ROLES]


# ---- 请求/响应模型 ----
class DocOut(BaseModel):
    # 不给默认值：_doc_json 恒返回全部键，spec 里就该是必现字段（有默认会被标成可选，
    # 前端类型随之变宽松——契约与实现的一致性优先于"以后可能加字段"的顾虑）
    id: int
    kb_id: int
    name: str
    status: DocStatus
    error: str | None
    size_bytes: int | None
    created_at: datetime        # 上传时间（UTC；列表按此展示，客户要能看出"什么时候传的"）


class BatchUploadItemOut(BaseModel):
    """批量上传的逐文件结果：部分成功语义——坏文件只在它自己那行有 error。"""
    name: str
    document: DocOut | None
    error: str | None


class ChunkPreviewOut(BaseModel):
    id: int
    chunk_index: int
    content: str
    has_embedding: bool
    meta: dict           # §A 分块预览是"为什么没答对"的第一工具：标出图片描述等来源


class KbIn(BaseModel):
    name: Utf8Str = Field(max_length=128)          # 修复⑨：varchar 列宽入约（PG String(128)）
    description: Utf8Str | None = None


class RetrieveIn(BaseModel):
    query: Utf8Str
    kb_ids: list[ReqId] | None = None
    top_k: JsonInt | None = None


class ChatIn(BaseModel):
    question: Utf8Str
    kb_ids: list[ReqId] | None = None
    conversation_id: ReqId | None = None
    images: list[Annotated[Utf8Str, Field(json_schema_extra={"pattern": IMAGE_PATTERN})]] | None = None


class DocPatchIn(BaseModel):
    status: DocStatus | None = None   # 状态机封闭集合（enum 进 spec，未知状态 422）
    error: Utf8Str | None = None


class ModelIn(BaseModel):
    # scenario 枚举约束写进 schema（pattern）：运行时仍走 400 业务校验，但契约生成器不再
    # 拿任意合法字符串撞出 400 被判"合法请求被拒"（fuzz 修复③：约束没进 spec 才是根因）
    scenario: str = Field(json_schema_extra={"pattern": SCENARIO_PATTERN})
    provider: Utf8Str = Field(max_length=32)              # 修复⑨：PG String(32)
    base_url: Utf8Str                                     # Text 无界
    api_key: Utf8Str                                      # 加密后落 Text
    model_name: Utf8Str = Field(max_length=128)           # 修复⑨：PG String(128)
    capabilities: JsonSafe = {}
    is_default: StrictBool = False
    fallback_rank: ReqId = 0
    enabled: StrictBool = True


class ModelPatchIn(BaseModel):
    scenario: Annotated[str, Field(json_schema_extra={"pattern": SCENARIO_PATTERN})] | None = None
    provider: Utf8Str | None = Field(None, max_length=32)
    base_url: Utf8Str | None = None
    api_key: Utf8Str | None = None
    model_name: Utf8Str | None = Field(None, max_length=128)
    capabilities: JsonSafe | None = None
    is_default: StrictBool | None = None
    fallback_rank: ReqId | None = None
    enabled: StrictBool | None = None


# ---- 内置厂商标本 + 一键配齐（客户不该被迫回答厂商实现细节）----
class CatalogProfileOut(BaseModel):
    id: str
    name: str
    base_url: str
    hint: str | None = None


class CatalogCapabilityOut(BaseModel):
    key: str                # 能力（给客户看的用途）：chat/embedding/vision/rerank
    scenario: str           # 落库时的场景（系统内部的路由键）
    model: str
    profile: str
    dim: JsonInt | None = None
    note: str | None = None


class CatalogVendorOut(BaseModel):
    id: str
    name: str
    aliases: list[str] = []
    pinyin: list[str] = []
    verified: bool = False
    verified_at: str | None = None
    profiles: list[CatalogProfileOut] = []
    capabilities: list[CatalogCapabilityOut] = []
    note: str | None = None


class ModelCatalogOut(BaseModel):
    catalog_version: str
    updated_at: str
    capability_labels: dict[str, str]
    capability_miss: dict[str, str]
    vendors: list[CatalogVendorOut]


class BundleIn(BaseModel):
    vendor_id: str = Field(max_length=32)
    api_key: Utf8Str
    capabilities: list[str] = []        # 勾选要启用的能力；空＝该厂商全部能力
    test: StrictBool = True             # 登记后各试调一次，把真实原因回给界面


class BundleItemOut(BaseModel):
    capability: str
    scenario: str
    model: str
    base_url: str
    action: str                         # created / updated（重复点不会堆出多份配置）
    ok: bool | None = None              # 试调结果：None=没试（test=False 或该能力未接线）
    detail: str | None = None


class BundleOut(BaseModel):
    vendor: str
    items: list[BundleItemOut]


class LoginIn(BaseModel):          # 邮箱+口令登录（RBAC spec §2）
    email: Utf8Str = Field(max_length=255)
    password: Utf8Str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    old_password: Utf8Str = Field(max_length=256)
    new_password: Utf8Str = Field(min_length=8, max_length=256)


class UserIn(BaseModel):
    email: Utf8Str = Field(max_length=255, pattern=EMAIL_PATTERN)
    name: Utf8Str = Field(default="", max_length=128)
    password: Utf8Str = Field(min_length=8, max_length=256)
    role: str = Field(default="member", json_schema_extra={"pattern": ROLE_PATTERN})
    daily_token_limit: NonNegInt | None = None      # null=不限（§C 用户级成本闸门）
    monthly_token_limit: NonNegInt | None = None


class UserPatchIn(BaseModel):
    name: Utf8Str | None = Field(None, max_length=128)
    role: str | None = Field(None, json_schema_extra={"pattern": ROLE_PATTERN})
    status: str | None = Field(None, json_schema_extra={"pattern": USER_STATUS_PATTERN})
    # 默认必须显式 None：Field 无默认=必填（同款写法见 ModelPatchIn），否则 patch 不带 password 即 422
    password: Utf8Str | None = Field(None, min_length=8, max_length=256)
    daily_token_limit: NonNegInt | None = None
    monthly_token_limit: NonNegInt | None = None


class GrantsIn(BaseModel):
    kb_ids: list[ReqId] = []


class ApiKeyIn(BaseModel):
    name: Utf8Str = Field(max_length=128)
    kb_ids: list[ReqId] | None = None            # null=全库；数组=限定库
    monthly_token_quota: JsonInt | None = None   # null=不限


class ApiKeyPatchIn(BaseModel):
    name: Utf8Str | None = Field(None, max_length=128)
    kb_ids: list[ReqId] | None = None
    monthly_token_quota: JsonInt | None = None
    enabled: StrictBool | None = None


class OpenAiMessageIn(BaseModel):
    role: Utf8Str
    content: Utf8Str


class OpenAiChatIn(BaseModel):
    model: Utf8Str | None = None
    messages: list[OpenAiMessageIn]


def _nonblank(v):
    if isinstance(v, str) and not v.strip():
        raise ValueError("不能为空白")
    return v


BrandName = Annotated[str, BeforeValidator(_nonblank), Field(max_length=64)]


class BrandingPut(BaseModel):
    # 白标（§2.2）：两字段都可选——只带其一即部分更新；logo 显式 null=清除（用 model_fields_set 区分缺席）
    brand_name: BrandName | None = None
    logo: Utf8Str | None = None


# ---- 响应模型（评审补齐：此前 33 个"会返回正文"的端点没有 schema，前端只能手写类型 + 强转，
# 后端改一个字段名不会有任何测试变红 —— "契约是唯一事实源"在这半边是空的）----
class HealthOut(BaseModel):
    status: str
    commit: str
    started_at: datetime


class AuthMeOut(BaseModel):
    email: str
    name: str
    role: RoleName
    kb_ids: list[int] | None      # null=admin 隐式全库
    must_change_password: bool


class KbOut(BaseModel):
    id: int
    name: str
    description: str | None


class HitOut(BaseModel):
    """检索命中的切块（/retrieve 的返回）。rerank_score 只在配置了精排时出现。"""
    id: int
    doc_name: str
    kb_id: int
    chunk_index: int
    content: str
    score: float
    bm25_hit: bool
    vec_hit: bool
    rerank_score: float | None = None


class CitationOut(BaseModel):
    n: int
    doc_name: str
    chunk_id: int
    excerpt: str


class ChatUsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class ChatOut(BaseModel):
    conversation_id: int
    answer: str
    citations: list[CitationOut]
    cited_docs: list[str]
    usage: ChatUsageOut
    # 兜底/降级成因（null=正常由模型作答）：no_hit=检索没命中、no_model=没有可用的 chat 模型、
    # model_error=模型调用失败。三种情况回的是同一句未命中兜底文案，界面只能靠这个字段区分——
    # 否则"模型挂了"会被读成"资料里没有"，人去重建索引、翻文档，白忙一场（真机 2026-10-09 踩中）。
    degraded_reason: str | None = None


class ConversationOut(BaseModel):
    id: int
    title: str
    kb_ids: list[int]


class ImageUrlOut(BaseModel):
    url: str


class MessagePartOut(BaseModel):
    """多模态消息片段。

    两个刻意的选择：①**字段全显式声明**（而不是把 image_url 丢给 extra 透传）——
    契约要能自己说清"消息可以带图"；代价是文本片段会带一个 `image_url: null`（前端 falsy 处理，无害）。
    ②**extra=allow**——片段是 JSONB 自由结构，以后加 type（文件、音频）或加字段时
    不能因为模型没声明就被 response_model 悄悄丢掉。"""
    model_config = ConfigDict(extra="allow")

    type: str
    text: str | None = None
    image_url: ImageUrlOut | None = None


class MessageOut(BaseModel):
    id: int
    role: MessageRoleName
    content: list[MessagePartOut]
    citations: list[CitationOut] | None


class ModelOut(BaseModel):
    id: int
    scenario: ScenarioName
    provider: str
    base_url: str
    model_name: str
    capabilities: dict
    is_default: bool
    fallback_rank: int
    enabled: bool
    api_key_masked: str          # 只回打码：明文 key 永不回传


class UsageSummaryOut(BaseModel):
    scenario: str
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int


class QuotaOut(BaseModel):
    """配额状态（/usage/me）：限额 null=不限。"""
    daily_used: int
    daily_limit: int | None
    monthly_used: int
    monthly_limit: int | None
    near_limit: bool
    exceeded: bool
    warn_ratio: float


class UsageUserOut(QuotaOut):
    id: int
    email: str
    name: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: RoleName
    status: UserStatusName
    created_at: str
    kb_ids: list[int] | None
    daily_token_limit: int | None
    monthly_token_limit: int | None


class AuditOut(BaseModel):
    id: int
    user_email: str | None
    action: str
    target_type: str | None
    target_id: int | None
    detail: dict
    ip: str | None
    created_at: datetime


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    kb_ids: list[int] | None
    monthly_token_quota: int | None
    enabled: bool
    last_used_at: str | None
    created_at: str


class ApiKeyCreatedOut(ApiKeyOut):
    key: str                    # 一次性明文：只在创建响应出现，此后任何接口都不回传


class BrandingOut(BaseModel):
    brand_name: str
    logo: str | None


class LicenseOut(BaseModel):
    enforced: bool
    valid: bool
    reason: str | None
    license_key: str | None
    customer: str | None
    issued_at: str | None
    expires_at: str | None
    days_left: int | None
    features: dict
    machine_fingerprint: str


class SettingsSnapshotOut(BaseModel):
    """配置中心快照：生效值 / 默认值 / 被覆盖的键 / 跨字段警告 / 标签与说明。"""
    values: dict[str, str | int | float | bool]
    defaults: dict[str, str | int | float | bool]
    overridden: list[str]
    warnings: list[str]
    labels: dict[str, str]
    help: dict[str, str]


class OpenAiMessageOut(BaseModel):
    role: str
    content: str


class OpenAiChoiceOut(BaseModel):
    index: int
    message: OpenAiMessageOut
    finish_reason: str


class OpenAiUsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAiChatOut(BaseModel):
    """OpenAI 兼容响应 + 本产品扩展（citations）：客户拿现成 SDK 就能接，又能溯源。"""
    id: str
    object: str
    created: int
    model: str
    choices: list[OpenAiChoiceOut]
    usage: OpenAiUsageOut
    citations: list[CitationOut]


# ---- 评测（§阶段2「评测体系正式化」）----
class EvalQuestionIn(BaseModel):
    question: Utf8Str                                # Text 列，无界
    expect_all: list[Utf8Str] = []
    expect_any: list[Utf8Str] = []
    cites: list[Utf8Str] = []                        # 期望命中的文档名（对照 DirtyDocs 现有文件名）
    category: Utf8Str = Field(default="", max_length=64)   # PG String(64) 列宽入约（同修复⑨）
    note: Utf8Str | None = None
    enabled: StrictBool = True


class EvalQuestionPatchIn(BaseModel):
    # 缺席=不动（与用户/模型 PATCH 同一套语义）；显式 null 也按"不动"处理——清空期望词请传空数组
    question: Utf8Str | None = None
    expect_all: list[Utf8Str] | None = None
    expect_any: list[Utf8Str] | None = None
    cites: list[Utf8Str] | None = None
    category: Utf8Str | None = Field(None, max_length=64)
    note: Utf8Str | None = None
    enabled: StrictBool | None = None


class EvalRunIn(BaseModel):
    kb_ids: list[ReqId] | None = None     # null=全部知识库（评测单个库是最常见用法）
    # 裁判模型（§阶段2 Ragas 侧）：开了就每题多调一次模型判 faithfulness/relevance。
    # 默认关——它花的是真金白银，且**永不并入通过率**（裁判会漂移，证据不能建立在会变的东西上）
    judge: StrictBool = False


class EvalQuestionOut(BaseModel):
    id: int
    question: str
    expect_all: list[str]
    expect_any: list[str]
    cites: list[str]
    category: str
    note: str | None
    enabled: bool
    created_at: datetime


class EvalCategoryOut(BaseModel):
    category: str = ""
    total: int = 0
    passed: int = 0


class EvalJudgeStatsOut(BaseModel):
    """裁判汇总（与确定性判据分栏呈现）。judged=判过的题数，scored=判词能解析的题数——
    两者不等说明裁判输出格式有问题，那是裁判的毛病，不该算在回答头上。"""
    judged: int = 0
    scored: int = 0
    faithful: int = 0
    relevance: int = 0
    faithful_rate: float = 0.0
    relevance_rate: float = 0.0
    model: str | None = None


class EvalJudgeVerdictOut(BaseModel):
    """单题判词（faithful/relevance 为 None=判词无法解析，不是 0 分）。"""
    faithful: int | None = None
    relevance: int | None = None
    reason: str = ""
    raw: str | None = None
    model: str | None = None


class EvalMetricsOut(BaseModel):
    """汇总指标（JSONB 里存的就是这个形状）。**全字段给默认值**：运行中的 run 其 metrics 还是空 {},
    响应模型必须把它补成全 0 而不是 500——"还没跑完"是正常状态，不是错误。
    形状写进 schema 而不是留 dict：前端才拿得到真类型，"指针随便点"的错误在编译期就没了。"""
    total: int = 0
    passed: int = 0
    pass_rate: float = 0.0
    with_cites: int = 0
    hit: int = 0
    hit_rate: float = 0.0
    mrr: float = 0.0
    avg_latency_ms: int = 0
    categories: list[EvalCategoryOut] = []
    judge: EvalJudgeStatsOut = Field(default_factory=EvalJudgeStatsOut)


class EvalChecksOut(BaseModel):
    """单题判据（同 JSONB）：retrieval=None 表示该题没设金标准文档（不适用，不是失败）。"""
    kw_all: bool = False
    kw_any: bool = False
    citation: bool = False
    retrieval: bool | None = None
    passed: bool = False
    rank: int = 0


class EvalRunOut(BaseModel):
    id: int
    status: EvalRunStatus
    total: int
    passed: int
    metrics: EvalMetricsOut
    kb_ids: list[int] | None
    judge: bool                  # 本轮是否请了裁判（区分"没启用"与"启用了但全判失败"）
    chat_model: str | None
    embedding_model: str | None
    error: str | None
    created_by: str | None
    started_at: datetime
    finished_at: datetime | None


class EvalItemOut(BaseModel):
    id: int
    question_id: int | None      # 题目被删后置空：明细仍完整（快照存档）
    question: str
    category: str
    note: str | None
    expect_all: list[str]
    expect_any: list[str]
    cites: list[str]
    answer: str | None
    cited_docs: list[str]
    top_docs: list[str]
    checks: EvalChecksOut
    judge: EvalJudgeVerdictOut | None = None
    passed: bool
    rank: int
    latency_ms: int | None
    error: str | None


class EvalRunDetailOut(EvalRunOut):
    items: list[EvalItemOut]


class EvalReportOut(BaseModel):
    markdown: str


class ReindexIn(BaseModel):
    kb_ids: list[ReqId] | None = None     # null=全部知识库（换 embedding 模型后真正需要的是全部）


class ReindexOut(BaseModel):
    documents: int                        # 本次将重建的文档数（前端据此显示"共 N 篇"）
    kb_ids: list[int] | None
    job_id: int                           # 作业号：进程被杀后靠它查"跑完没有"


class JobScopeOut(BaseModel):
    kb_ids: list[int] | None


class JobOut(BaseModel):
    """后台作业（评审补齐）：与文档状态机分工——作业说"谁为什么发起"，文档说"跑到哪了"。"""
    id: int
    kind: str
    status: JobStatusName
    scope: JobScopeOut
    total: int
    done: int
    failed: int
    error: str | None
    created_by: str | None
    started_at: datetime
    finished_at: datetime | None


# 契约 fuzz 前提：真实错误码必须写进 spec，否则 schemathesis 判合法响应为违约
class ErrorOut(BaseModel):
    detail: str


ERR = lambda code, msg: {code: {"model": ErrorOut, "description": msg}}  # noqa: E731
# fuzz 修复④：请求体不是合法 JSON 时 Starlette 直接回 400（FastAPI 默认 spec 只带 422）——按实声明
ERR_BODY = ERR(400, "请求体解析失败")

ERR_UNAUTH, ERR_FORBID = ERR(401, "需要登录"), ERR(403, "需要管理员权限")
ERR_MUST_CHANGE = ERR(428, "首次登录必须修改初始口令")
# ERR_GATE：admin 面全量（401+403+428）；ERR_LOGIN_GATE：member 面（401+428，无 403——
# 403 声明面必须仍与 admin 面精确重合，双轨守卫不被稀释）
ERR_GATE = {**ERR_UNAUTH, **ERR_FORBID, **ERR_MUST_CHANGE}
ERR_LOGIN_GATE = {**ERR_UNAUTH, **ERR_MUST_CHANGE}


def build_settings_put_model(spec: dict[str, Spec]):
    """配置中心请求体：按 SPEC 动态建模——单字段约束自动进 OpenAPI schema
    （additionalProperties=false → 未知键 422；长度/范围由 Field 表达），
    前端与 fuzz 都拿得到真实约束，"约束没进 spec" 的坑不再重演。"""
    from typing import Optional as _Opt

    fields: dict = {}
    for key, sp in spec.items():
        t = {"float": float, "int": int, "bool": bool}.get(sp.kind, str)
        kw: dict = {"default": None, "description": sp.label}
        if sp.max_length is not None:
            kw["max_length"] = sp.max_length
        if sp.minimum is not None:
            kw["ge"] = sp.minimum
        if sp.maximum is not None:
            kw["le"] = sp.maximum
        fields[key] = (_Opt[t], Field(**kw))
    return pydantic_create_model("SettingsPutIn", __config__=ConfigDict(extra="forbid"),
                                 **fields)
