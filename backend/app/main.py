# FastAPI 服务层：知识库/文档入库/检索/带引用问答/会话历史
# 鉴权收口（阶段 2·任务 6）：除 /health 与 /auth/login 外全部端点要求登录会话；
# admin 面（kb 写/文档写/models/usage/users/grants/audit）另加角色校验，member 得 403。
# 库级授权在检索层钳制（services/retrieval.allowed_kb_ids），不在展示层过滤。
# 旧 ADMIN_TOKEN 共享口令方案（require_admin/admin_session/admin_hint//admin/*）已整体退役，
# 不留兼容层；初始管理员由 build_production_app 按 ADMIN_EMAIL/ADMIN_PASSWORD 播种。
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import logging
import re
import threading
import time

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, StrictBool,
                      create_model as pydantic_create_model)  # 端点函数名 create_model 会遮蔽同名导入
from sqlalchemy import func, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.models import (ApiKey, AppSetting, AuditLog, Chunk, Conversation, Document,
                        EvalItemResult, EvalQuestion, EvalRun, KnowledgeBase, Message,
                        ModelConfig, User, UserKbGrant, UserSession, UsageRecord)
from app.services.audit import record as audit_record
from app.services.auth import (LoginThrottle, hash_password, new_api_key, new_session_token,
                               token_digest, verify_password)
from app.services.citations import parse_citations
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.evaluation import aggregate, check_item, load_golden, render_report
from app.services.ingest import ingest_document, read_stored, supported_ext
from app.services.judge import judge_stats, make_judge_fn
from app.services.license import load_license_status, machine_fingerprint
from app.services.retrieval import retrieve
from app.services.settings import DEFAULT_MISS_ANSWER, SettingsStore
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


def _build_commit() -> str:
    """当前代码版本：容器部署可用 BUILD_COMMIT 注入（镜像内无 .git），开发态直接问 git。"""
    import os
    import subprocess

    env = (os.environ.get("BUILD_COMMIT") or "").strip()
    if env:
        return env[:40]
    try:
        repo = Path(__file__).resolve().parents[2]
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                             capture_output=True, text=True, timeout=3).stdout.strip()
        if not sha:
            return "unknown"
        # 工作区有未提交改动时标 -dirty：排查"改了不生效"时不该把未提交的改动误当成已生效
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                               capture_output=True, text=True, timeout=3).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except Exception:
        return "unknown"


BUILD_COMMIT = _build_commit()


def ensure_vector_extension(engine: Engine) -> None:
    """建表前必须先有的东西：pgvector 扩展。

    真机踩过（2026-10-08，本地 Postgres.app 起全新库）：`chunks.embedding` 是 VECTOR(1024) 列，
    扩展不在就直接 `type "vector" does not exist` —— 一键部署会死在 create_all 这一步。
    pgvector/pgvector 镜像只是**扩展可用**，不是**已安装**，全新数据卷同样会踩。
    IF NOT EXISTS 幂等：客户 DBA 预装过就空转（PG 先判存在即跳过，不会因权限被拦）。
    """
    with engine.begin() as con:
        con.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))


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
# 响应侧枚举（评审补齐）：契约要能自己说清 role 只有两个值——此前是裸 string，
# 前端手写联合类型只能算"口头约定"，而且两边都不报错（耦合断言一上来就抓到了这类漂移）
RoleName = Literal[*sorted(ROLES)]
UserStatusName = Literal[*sorted(USER_STATUSES)]
ScenarioName = Literal[*sorted(SCENARIOS)]
MessageRoleName = Literal[*MESSAGE_ROLES]


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


# 传图提问（阶段 2 放开）：data URL 直存消息 content part（与白标 logo 同闸：类型白名单+大小上限）
IMAGE_PATTERN = r"^data:image/(?:png|jpeg|jpg|webp|gif|svg\+xml);base64,"
IMAGE_MAX = 2_000_000        # 单张 base64 字符上限 ≈ 1.5MB 二进制
IMAGE_COUNT_MAX = 3


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


class LoginIn(BaseModel):          # 邮箱+口令登录（RBAC spec §2）
    email: Utf8Str = Field(max_length=255)
    password: Utf8Str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    old_password: Utf8Str = Field(max_length=256)
    new_password: Utf8Str = Field(min_length=8, max_length=256)


# 邮箱格式约束进 spec（pattern 自动入 schema，422 由 FastAPI 默认声明）：
# 要求 @ 后至少带一个点分的域名标签（"a@x"、"a@.com" 都拒），+ 号路由标签放行
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


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
# 约定：这里的字段必须与端点真正返回的键**逐字对齐**。pydantic 会**默默丢掉**模型里没声明的字段
# （评测 run 的 judge 字段就这样消失过一次），所以改动后要用 /tmp/snapshot_responses.py 做字段快照 diff。
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


# 契约 fuzz 前提：真实错误码必须写进 spec，否则 schemathesis 判合法响应为违约
class ErrorOut(BaseModel):
    detail: str


_ERR = lambda code, msg: {code: {"model": ErrorOut, "description": msg}}  # noqa: E731
# fuzz 修复④：请求体不是合法 JSON 时 Starlette 直接回 400（FastAPI 默认 spec 只带 422）——按实声明
_ERR_BODY = _ERR(400, "请求体解析失败")

MISS_ANSWER = DEFAULT_MISS_ANSWER   # 文案单一事实源在 services/settings（后台可改）


def with_gateway_fallback(gw_fn: Callable | None,
                          direct_fn: Callable | None) -> Callable | None:
    """运行时换模型即生效（§C）的组合装配：网关 fn 每次调用现读 model_configs，
    表里还没有启用模型（NoProviderError）时回退 .env 直连；两侧都没有 → None
    （问答端点按未命中兜底）。配了但全挂的 GatewayError 原样上抛，由端点转兜底。"""
    from app.services.gateway import GatewayError, NoProviderError

    if gw_fn is None:
        return direct_fn
    if direct_fn is None:
        def gw_only(query, hits):
            try:
                return gw_fn(query, hits)
            except NoProviderError:
                return None   # 无直连可退：None 由问答端点按未命中兜底处理
        return gw_only
    if direct_fn is None:
        return gw_fn

    def composed(query, hits):
        try:
            return gw_fn(query, hits)
        except NoProviderError:
            return direct_fn(query, hits)

    return composed


def with_rerank_fallback(gw_fn: Callable | None, direct_fn: Callable | None) -> Callable | None:
    """组合两路精排（网关优先、.env 原生端点兜底）。

    与 chat 的 `with_gateway_fallback` 形状相似但**语义必须不同**：chat 在"无供应商"时返回 None，
    含义是"没有答案"；而精排**返回 None 会把检索整条打断**（retrieve 会拿 None 去切片）。
    真机踩过：复用 chat 那个组合器后，网关表里没登记 rerank 时 /retrieve 直接 500。
    所以精排的"NoProviderError"语义是"这一路不可用"，交给另一路或原样返回候选。
    """
    from app.services.gateway import NoProviderError

    if gw_fn is None:
        return direct_fn
    if direct_fn is None:
        return gw_fn

    def composed(query: str, hits: list[dict]) -> list[dict]:
        try:
            return gw_fn(query, hits)
        except NoProviderError:
            return direct_fn(query, hits)

    return composed


def with_rerank_degrade(rerank_fn: Callable | None) -> Callable | None:
    """重排失败降级为**未重排的顺序**，只记 warning。

    与 chat 的口径刻意不同：chat 挂了就没有答案，必须上抛让端点走未命中兜底；
    而重排只是检索链路里"锦上添花"的一步，它挂了还答得出来——为此把整个问答弄失败不合理。
    这类失败（上游抖动/模型被停用）不影响数据正确性，记 warning 足够，不该惊动用户。
    """
    if rerank_fn is None:
        return None

    def wrapped(query: str, hits: list[dict]) -> list[dict]:
        try:
            return rerank_fn(query, hits)
        except Exception as exc:
            logging.getLogger("umax").warning("重排失败，改用未重排顺序：%s", exc)
            return hits

    return wrapped


class FallbackEmbedder:
    """组合 embedder（网关优先、.env 百炼兜底）：网关表空（NoProviderError）退直连；
    两侧都无 → 返回 [None]*n——ingest 语义即"不向量化 BM25-only 入库"，
    后台登记 embedding 模型后点重处理即可补向量，无需重启。"""

    def __init__(self, gw_embedder, direct_embedder) -> None:
        self._gw, self._direct = gw_embedder, direct_embedder

    def embed(self, texts):
        from app.services.gateway import NoProviderError

        try:
            return self._gw.embed(texts)
        except NoProviderError:
            if self._direct is None:
                return [None] * len(texts)
            return self._direct.embed(texts)


class _AllowHeaderMiddleware:
    """fuzz 修复⑤：同一路径由多个单方法路由拼成，Starlette 的 405/OPTIONS 只报首个路由的方法
    （AllowHeaderMismatch 抓到 Allow: POST 少了 GET，违反 RFC 9110）——合并为路径真实方法全集。"""

    def __init__(self, app: ASGIApp, routes) -> None:
        self.app, self.routes = app, routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        methods: set[str] = set()
        if scope["type"] == "http":
            for route in self.routes:
                allowed = getattr(route, "methods", None)
                if allowed:
                    matched = route.matches(scope)
                    match = matched[0] if isinstance(matched, tuple) else matched
                    if match in (Match.FULL, Match.PARTIAL):
                        methods |= allowed
        if not methods:
            await self.app(scope, receive, send)
            return

        async def send_with_allow(message: dict) -> None:
            if message["type"] == "http.response.start" and (
                    message["status"] == 405 or scope["method"] == "OPTIONS"):
                headers = MutableHeaders(raw=message["headers"])
                headers["allow"] = ", ".join(sorted(methods))
            await send(message)

        await self.app(scope, receive, send_with_allow)


def _doc_json(d: Document) -> dict:
    return {"id": d.id, "kb_id": d.kb_id, "name": d.name, "status": d.status,
            "error": d.error, "size_bytes": d.size_bytes,
            "created_at": d.created_at}


def create_app(
    *,
    engine: Engine,
    embedder=None,
    chat_fn: Callable[[str, list[dict]], dict] | None = None,
    vision_fn: Callable[[str], dict] | None = None,   # (image_data_url) -> {caption,...}；None=无视觉模型
    upload_dir: str = "uploads",
    queue=None,          # ImportQueue 协议；None=同步入库
    mineru=None,         # MinerUClient；None=扫描件解析直接失败并说明原因
    secret: str | None = None,  # 网关主密钥（GATEWAY_SECRET），None=读配置
    license_public_key: str | None = None,   # License 公钥；空=开发模式不校验
    license_file: str | None = None,         # 授权文件路径
    machine_fingerprint: str | None = None,  # 本机指纹（测试注入；None=现算）
    settings_store=None,     # 运行时配置中心；None=按 engine+.env 现建
    spawn: Callable[[Callable[[], None]], None] | None = None,
    # 后台任务启动器（评测用）。None=真线程；测试注入同步实现（lambda fn: fn()），
    # 让"后台跑完再看结果"在单测里是确定性的——不靠 sleep 赌时序
    judge_fn: Callable[[str, str, list[dict]], dict] | None = None,
    # LLM 裁判（§阶段2 Ragas 侧）：(question, answer, hits) -> 判词；None=未配置裁判通路（请求评测带 judge 即 400）
    rerank_fn: Callable[[str, list[dict]], list[dict]] | None = None,
    # 精排（§3.2）：(query, 候选) -> 重排后的候选；None=未配置 rerank 模型（检索就用 RRF 融合顺序）
) -> FastAPI:
    app = FastAPI(title="Umax RAG", version="0.1.0")
    s = get_settings()
    gateway_secret = s.gateway_secret if secret is None else secret

    def get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    # 运行时配置中心（§D）：提示词/检索/切块参数后台可改，每请求现读＝改完即生效
    cfg = settings_store if settings_store is not None else SettingsStore(engine, s)

    def _spawn_default(fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    spawn_fn = spawn or _spawn_default
    # 精排：装上"失败即降级"的外壳，四处检索调用共用同一份（口径只有一处）
    rerank = with_rerank_degrade(rerank_fn)

    # ==================== RBAC（spec 2026-09-23）：账号+DB 会话+库级授权 ====================
    SESSION_COOKIE, SESSION_TTL_DAYS = "umax_session", 7
    throttle = LoginThrottle()   # 每 app 实例独立计数：测试互污染为零

    _ERR_UNAUTH, _ERR_FORBID = _ERR(401, "需要登录"), _ERR(403, "需要管理员权限")
    # 首登强改密（初始化向导收尾）：口令非本人设定的账号未改密前，全部受护端点 428——
    # 与 403 分轨（403 专属 admin 面，双轨守卫不被稀释），豁免仅 auth 三件套：
    # me（前端靠它知道该弹改密框）/logout（随时可走人）/change-password（解除门闸的唯一通道）
    MUST_CHANGE_EXEMPT = ("/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/change-password")
    _ERR_MUST_CHANGE = _ERR(428, "首次登录必须修改初始口令")
    # _ERR_GATE：admin 面全量（401+403+428）；_ERR_LOGIN_GATE：member 面（401+428，无 403——
    # 403 声明面必须仍与 admin 面精确重合，双轨守卫不被稀释）
    _ERR_GATE = {**_ERR_UNAUTH, **_ERR_FORBID, **_ERR_MUST_CHANGE}
    _ERR_LOGIN_GATE = {**_ERR_UNAUTH, **_ERR_MUST_CHANGE}

    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def get_user(request: Request, session: Session = Depends(get_session)) -> User:
        """cookie→会话→账号三点一线，任一断 401（禁用的存活会话也断在这）——所有受护端点的统一依赖。
        首登强改密：口令非本人设定（must_change_password）未改密前，除 auth 三件套外一律 428。"""
        token = request.cookies.get(SESSION_COOKIE)
        row = session.get(UserSession, token_digest(token)) if token else None
        user = session.get(User, row.user_id) if row else None
        if not row or row.expires_at <= _now() or not user or user.status != "active":
            raise HTTPException(401, "需要登录")
        if user.must_change_password and request.url.path not in MUST_CHANGE_EXEMPT:
            raise HTTPException(428, "首次登录必须修改初始口令")
        request.state.user, request.state.session_hash = user, row.token_hash
        return user

    def require_admin_role(user: User = Depends(get_user)) -> User:
        if user.role != "admin":
            raise HTTPException(403, "需要管理员权限")
        return user

    # ---- License 授权（§D）：每请求现读文件——续期换文件立即生效、签名即信任根 ----
    lic_public_key = s.license_public_key if license_public_key is None else license_public_key
    lic_file = Path(license_file) if license_file else s.license_file
    lic_fingerprint = machine_fingerprint or None

    def license_status():
        return load_license_status(lic_file, lic_public_key, fingerprint=lic_fingerprint)

    def require_license(admin: User = Depends(require_admin_role)) -> User:
        """写操作门闸：授权失效即只读（读与问答照常，改密不受影响）——续费压力落在管理动作上。"""
        st = license_status()
        if st.enforced and not st.valid:
            raise HTTPException(403, f"授权不可用：{st.reason}")
        return admin

    @app.get("/api/v1/license", response_model=LicenseOut, responses={**_ERR_GATE})
    def get_license(admin: User = Depends(require_admin_role)):
        st = license_status()
        return {"enforced": st.enforced, "valid": st.valid, "reason": st.reason,
                "license_key": st.license_key, "customer": st.customer,
                "issued_at": st.issued_at, "expires_at": st.expires_at,
                "days_left": st.days_left, "features": st.features,
                "machine_fingerprint": st.machine_fingerprint}

    def allowed_kb_ids(session: Session, user: User) -> set[int] | None:
        """None=admin 隐式全库；member=授权集合（可为空集——消费端必须区分 None 与 set()）。"""
        if user.role == "admin":
            return None
        return {g.kb_id for g in session.query(UserKbGrant).filter_by(user_id=user.id)}

    @app.post("/api/v1/auth/login", status_code=204,
              responses={**_ERR(401, "邮箱或口令错误"),
                         **_ERR(429, "失败次数过多，15 分钟后再试"), **_ERR_BODY})
    def auth_login(body: LoginIn, request: Request, response: Response,
                   session: Session = Depends(get_session)):
        key = f"{body.email}|{_client_ip(request)}"
        if throttle.blocked(key):
            # 文案与 spec 里 429 的 description 一字对齐（评审收编③）：同一码两处措辞必然漂移
            raise HTTPException(429, "失败次数过多，15 分钟后再试")
        u = session.query(User).filter_by(tenant_id="default", email=body.email).first()
        if not u or not verify_password(body.password, u.hashed_password) or u.status != "active":
            throttle.failure(key)
            audit_record(session, "login_failed", user_email=body.email, ip=_client_ip(request))
            session.commit()   # 审计必须先落，再抛 401
            raise HTTPException(401, "邮箱或口令错误")
        throttle.success(key)
        plain, token_hash = new_session_token()
        session.add(UserSession(token_hash=token_hash, user_id=u.id,
                                expires_at=_now() + timedelta(days=SESSION_TTL_DAYS)))
        audit_record(session, "login_success", user_email=u.email, target_type="user",
                     target_id=u.id, ip=_client_ip(request))
        session.commit()
        # secure 标记留生产硬化：HTTPS 终结在反代后时加 secure=True 即可（评审收编⑤）；
        # 当前 dev/测试是 http://127.0.0.1，加了 cookie 直接被丢弃，全链路登录态失效
        response.set_cookie(SESSION_COOKIE, plain, httponly=True, samesite="lax",
                            max_age=SESSION_TTL_DAYS * 24 * 3600, path="/")

    @app.post("/api/v1/auth/logout", status_code=204, responses=_ERR_UNAUTH)
    def auth_logout(request: Request, response: Response,
                    user: User = Depends(get_user), session: Session = Depends(get_session)):
        row = session.get(UserSession, request.state.session_hash)
        if row:
            session.delete(row)
        audit_record(session, "logout", user_email=user.email, ip=_client_ip(request))
        session.commit()
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.get("/api/v1/auth/me", response_model=AuthMeOut, responses=_ERR_UNAUTH)
    def auth_me(user: User = Depends(get_user), session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        return {"email": user.email, "name": user.name, "role": user.role,
                "kb_ids": None if allowed is None else sorted(allowed),
                "must_change_password": user.must_change_password}

    @app.post("/api/v1/auth/change-password", status_code=204,
              # 422 不覆写：FastAPI 默认 422（HTTPValidationError，detail 是数组）——用 _ERR(ErrorOut)
              # 覆写会被 fuzz 判 response_schema_conformance 违约（JSON 解析失败先于依赖 401 发生）。
              # 401 合并顺序（评审收编②）：_ERR_UNAUTH 在前、业务文案在后——同一状态码只有一个
              # description，端点专属的"旧口令错误"必须胜过通用"需要登录"（后者由 detail 承载）。
              responses={**_ERR_UNAUTH, **_ERR(401, "旧口令错误"), **_ERR_BODY})
    def auth_change_password(body: ChangePasswordIn, request: Request,
                             user: User = Depends(get_user), session: Session = Depends(get_session)):
        if not verify_password(body.old_password, user.hashed_password):
            raise HTTPException(401, "旧口令错误")
        user.hashed_password = hash_password(body.new_password)
        user.must_change_password = False   # 本人设定了新口令，门闸解除
        session.query(UserSession).filter(
            UserSession.user_id == user.id,
            UserSession.token_hash != request.state.session_hash).delete()   # 踢其他设备，留当前
        audit_record(session, "user_updated", user_email=user.email, target_type="user",
                     target_id=user.id, detail={"fields": ["self_password"]}, ip=_client_ip(request))
        session.commit()

    # ---- 用户管理 + 库级授权（spec §3.1，任务 4）：admin 独占，写操作全审计 ----
    def _grants_map(session: Session) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for g in session.query(UserKbGrant).order_by(UserKbGrant.kb_id):
            out.setdefault(g.user_id, []).append(g.kb_id)
        return out

    def _user_json(u: User, grants: dict[int, list[int]]) -> dict:
        return {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
                "status": u.status, "created_at": u.created_at.isoformat(),
                "kb_ids": None if u.role == "admin" else grants.get(u.id, []),
                "daily_token_limit": u.daily_token_limit,
                "monthly_token_limit": u.monthly_token_limit}

    def _revoke_sessions(session: Session, user_id: int, *, except_hash: str | None = None) -> None:
        q = session.query(UserSession).filter_by(user_id=user_id)
        if except_hash:
            q = q.filter(UserSession.token_hash != except_hash)
        q.delete()

    @app.get("/api/v1/users", response_model=list[UserOut], responses={**_ERR_GATE})
    def list_users(admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        g = _grants_map(session)
        return [_user_json(u, g) for u in session.query(User).order_by(User.id)]

    @app.post("/api/v1/users", status_code=201, response_model=UserOut,
              responses={**_ERR(400, "email 重复或 role 非法"), **_ERR_GATE, **_ERR_BODY})
    def create_user_api(body: UserIn, request: Request,
                        admin: User = Depends(require_license), session: Session = Depends(get_session)):
        if body.role not in ROLES:
            raise HTTPException(400, "role 仅支持 admin/member")
        if session.query(User).filter_by(tenant_id="default", email=body.email).first():
            raise HTTPException(400, "该邮箱已存在")
        u = User(tenant_id="default", email=body.email, name=body.name or body.email.split("@")[0],
                 hashed_password=hash_password(body.password), role=body.role, status="active",
                 must_change_password=True,   # 口令是 admin 代发的，首登必须本人改
                 daily_token_limit=body.daily_token_limit,
                 monthly_token_limit=body.monthly_token_limit)
        session.add(u)
        session.flush()
        audit_record(session, "user_created", user_email=admin.email, target_type="user",
                     target_id=u.id, detail={"email": u.email, "role": u.role}, ip=_client_ip(request))
        session.commit()
        return _user_json(u, _grants_map(session))

    @app.patch("/api/v1/users/{user_id}", response_model=UserOut,
               responses={**_ERR(400, "不能对当前登录管理员降级/禁用，role/status 非法"),
                          **_ERR(404, "用户不存在"), **_ERR_GATE, **_ERR_BODY})
    def patch_user(user_id: PathId, body: UserPatchIn, request: Request,
                   admin: User = Depends(require_license), session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.id == admin.id and (body.role == "member" or body.status == "disabled"):
            raise HTTPException(400, "不能对当前登录管理员降级或禁用")
        # 终审收口②：守卫是 `is not None` 而非真值判定——schema 里的 ROLE_PATTERN 只是
        # json_schema_extra（生成契约用，不参与校验），空串必须在这里吃 400：否则它跳过校验
        # 又被下面的 `if v is not None` 写循环当真值落库，账号变成不匹配任何角色判定的 role=""。
        if body.role is not None and body.role not in ROLES:
            raise HTTPException(400, "role 仅支持 admin/member")
        if body.status is not None and body.status not in USER_STATUSES:
            raise HTTPException(400, "status 仅支持 active/disabled")
        changed: list[str] = []
        for f in ("name", "role", "status"):
            v = getattr(body, f)
            if v is not None and v != getattr(u, f):
                setattr(u, f, v)
                changed.append(f)
        # 配额：显式带上的字段才动（含 null=清空为不限）——缺席≠清空
        for f in ("daily_token_limit", "monthly_token_limit"):
            if f in body.model_fields_set and getattr(u, f) != getattr(body, f):
                setattr(u, f, getattr(body, f))
                changed.append(f)
        if body.password:
            u.hashed_password = hash_password(body.password)
            changed.append("password")
            # 重置的口令是 admin 输的：目标用户回到门闸后；admin 重置自己不算（口令仍是本人输的，自锁无意义）
            if u.id != admin.id:
                u.must_change_password = True
        if {"role", "status", "password"} & set(changed):
            # 角色/启停/重置口令变更一律吊销（spec §2）；管理员自重置保留当前会话（评审收编⑦）——
            # 与自助改密同语义：吊销的是"其他设备"，不是把操作者从正在做的管理动作里踢出去。
            # 自降级/自禁用在上游已 400，故 except_hash 只会因 password 生效。
            _revoke_sessions(session, u.id,
                             except_hash=request.state.session_hash if u.id == admin.id else None)
        if changed:
            audit_record(session, "user_updated", user_email=admin.email, target_type="user",
                         target_id=u.id, detail={"fields": changed}, ip=_client_ip(request))
        session.commit()
        return _user_json(u, _grants_map(session))

    @app.delete("/api/v1/users/{user_id}", status_code=204,
                responses={**_ERR(400, "不能删除当前登录管理员"), **_ERR(404, "用户不存在"),
                           **_ERR_GATE})
    def delete_user(user_id: PathId, request: Request,
                    admin: User = Depends(require_license),
                    session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.id == admin.id:
            raise HTTPException(400, "不能删除当前登录管理员")
        # 连带清理：本人会话/消息（保密与干净卸载）；sessions/grants 走 FK CASCADE
        session.query(Conversation).filter_by(user_email=u.email).delete()
        email = u.email
        session.delete(u)
        audit_record(session, "user_deleted", user_email=admin.email, target_type="user",
                     target_id=user_id, detail={"email": email}, ip=_client_ip(request))
        session.commit()

    @app.get("/api/v1/users/{user_id}/grants", response_model=list[int],
             responses={**_ERR(400, "管理员隐式全库，无授权表"), **_ERR(404, "用户不存在"),
                        **_ERR_GATE})
    def get_grants(user_id: PathId, admin: User = Depends(require_admin_role),
                   session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.role == "admin":
            raise HTTPException(400, "管理员隐式全库，无授权表")
        return sorted(_grants_map(session).get(u.id, []))

    @app.put("/api/v1/users/{user_id}/grants", response_model=list[int],
             responses={**_ERR(400, "管理员无授权表 / 知识库不存在"), **_ERR(404, "用户不存在"),
                        **_ERR_GATE, **_ERR_BODY})
    def put_grants(user_id: PathId, body: GrantsIn, request: Request,
                   admin: User = Depends(require_license), session: Session = Depends(get_session)):
        u = session.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.role == "admin":
            raise HTTPException(400, "管理员隐式全库，无授权表")
        ids = sorted(set(body.kb_ids))
        if ids and len(session.query(KnowledgeBase.id).filter(KnowledgeBase.id.in_(ids)).all()) != len(ids):
            raise HTTPException(400, "存在不存在的知识库 id")
        session.query(UserKbGrant).filter_by(user_id=u.id).delete()
        for k in ids:
            session.add(UserKbGrant(user_id=u.id, kb_id=k))
        audit_record(session, "grants_updated", user_email=admin.email, target_type="user",
                     target_id=u.id, detail={"kb_ids": ids}, ip=_client_ip(request))
        session.commit()
        return ids

    # ---- 审计查询（spec §5，任务 5）：admin 独占，过滤+分页，倒序 ----
    @app.get("/api/v1/audit", response_model=list[AuditOut], responses={**_ERR_GATE})
    def list_audit(user: Annotated[str | None, Query(max_length=255)] = None,
                   action: Annotated[str | None, Query(max_length=32)] = None,
                   limit: QueryInt = 50, offset: QueryInt = 0,
                   admin: User = Depends(require_admin_role),
                   session: Session = Depends(get_session)):
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        q = session.query(AuditLog)
        if user:
            q = q.filter(AuditLog.user_email == user)
        if action:
            q = q.filter(AuditLog.action == action)
        # (created_at, id) 双键倒序：同事务内 created_at 相同（PG now()=事务起始时刻），
        # 只按时间排会让同批行的分页顺序不确定——id 兜底后全序确定，分页可复现
        rows = (q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset(offset).limit(limit).all())
        return [{"id": r.id, "user_email": r.user_email, "action": r.action,
                 "target_type": r.target_type, "target_id": r.target_id, "detail": r.detail,
                 "ip": r.ip, "created_at": r.created_at.isoformat()} for r in rows]

    started_at = _now().isoformat()

    @app.get("/api/v1/health", response_model=HealthOut)
    def health(session: Session = Depends(get_session)):
        session.execute(sa_text("SELECT 1"))
        # commit/started_at：排查"改了不生效"的第一手信息——先确认进程跑的是哪份代码
        return {"status": "ok", "commit": BUILD_COMMIT, "started_at": started_at}

    # ---- 知识库 ----
    def _guard_kb_ids(allowed: set[int] | None, kb_ids: list[int] | None) -> None:
        """显式点了未授权库 → 403（admin 的 allowed 是 None，直通）。"""
        if allowed is not None and not set(kb_ids or []) <= allowed:
            raise HTTPException(403, "无权访问指定知识库")

    @app.post("/api/v1/kb", status_code=201, response_model=KbOut,
              responses={**_ERR_BODY, **_ERR_GATE})
    def create_kb(body: KbIn, request: Request,
                  admin: User = Depends(require_license),
                  session: Session = Depends(get_session)):
        # 建库时锁定切分参数（§3.3）：取配置中心的生效值快照，此后改全局配置不影响已建的库
        kb = KnowledgeBase(tenant_id="default", name=body.name, description=body.description,
                           embedding_model=s.embedding_model,
                           chunk_target=cfg.effective()["chunk_target"])
        session.add(kb)
        session.flush()
        audit_record(session, "kb_created", user_email=admin.email,
                     target_type="kb", target_id=kb.id, detail={"name": body.name},
                     ip=_client_ip(request))
        session.commit()
        return {"id": kb.id, "name": kb.name, "description": kb.description}

    @app.get("/api/v1/kb", response_model=list[KbOut], responses={**_ERR_LOGIN_GATE})
    def list_kb(user: User = Depends(get_user), session: Session = Depends(get_session)):
        # member 的库列表在查询层过滤（不是前端隐藏）——空授权=空列表
        allowed = allowed_kb_ids(session, user)
        q = session.query(KnowledgeBase)
        if allowed is not None:
            q = q.filter(KnowledgeBase.id.in_(allowed))
        return [{"id": k.id, "name": k.name, "description": k.description}
                for k in q.order_by(KnowledgeBase.id)]

    @app.delete("/api/v1/kb/{kb_id}", status_code=204,
                responses={**_ERR(404, "知识库不存在"), **_ERR_GATE})
    def delete_kb(kb_id: PathId, request: Request,
                  admin: User = Depends(require_license),
                  session: Session = Depends(get_session)):
        """删库：文档/切块/授权级联清，落盘原文件一并删。**用量台账保留**（kb_id 置 NULL）——
        成本账是事实来源，不能随库消失；API key 作用域与历史会话里的孤儿 kb_id 天然无害
        （召回走交集语义），不做事后清理。不可逆，前端要求输入库名二次确认。
        """
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        name = kb.name
        files = [d.storage_path for d in session.query(Document).filter_by(kb_id=kb_id)]
        doc_count = len(files)
        session.delete(kb)      # documents/chunks/user_kb_grants 走 FK CASCADE
        audit_record(session, "kb_deleted", user_email=admin.email,
                     target_type="kb", target_id=kb_id,
                     detail={"name": name, "documents": doc_count}, ip=_client_ip(request))
        session.commit()
        for f in files:
            if not f:
                continue
            try:
                Path(f).unlink(missing_ok=True)
            except OSError as exc:   # 文件删不掉不该让接口失败：DB 已一致，残留文件无害
                import logging

                logging.getLogger("umax").warning("原文件清理失败 %s：%s", f, exc)

    @app.get("/api/v1/kb/{kb_id}/documents", response_model=list[DocOut],
             responses={**_ERR(404, "知识库不存在"), **_ERR_LOGIN_GATE})
    def list_documents(kb_id: PathId, user: User = Depends(get_user),
                       session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        # 不在授权范围内的库与"不存在"同文案：探测不出别人的库 id 存不存在
        if not session.get(KnowledgeBase, kb_id) or (allowed is not None and kb_id not in allowed):
            raise HTTPException(404, "知识库不存在")
        return [_doc_json(d) for d in session.query(Document)
                .filter_by(kb_id=kb_id).order_by(Document.id)]

    # ---- 文档与入库 ----
    BATCH_MAX = 20   # 单批文件数上限：挡住"一次传一个文件夹"把单请求打成长时间占用

    def _read_or_fail(session: Session, doc: Document) -> bytes | None:
        """读原始文件；读不出来就把文档标 failed 并返回 None。

        评审踩过：`read_stored` 抛在 ingest 的 try 之外，于是原文件被删/磁盘满/运维清过 uploads 时——
        同步档是未声明的 500、异步档是任务失败重试——而**文档行永远停在 pending**：界面一直"排队中"，
        没有线索指向"原文件没了"。失败态本身就是排查信息，四个入口（上传/批量/重处理/重建）口径必须一致。
        """
        try:
            return read_stored(doc)
        except OSError as exc:
            doc.status, doc.error = "failed", f"原文件不可读：{exc}"
            session.commit()
            return None

    def _store_upload(session: Session, kb: KnowledgeBase, file: UploadFile,
                      request: Request, admin: User) -> tuple[Document | None, str, str | None]:
        """落盘 + 建 pending 文档 + 审计（不入库/不入队，由调用方决定后续）。返回 (doc, name, error)。

        修复⑧收口：multipart filename 不经 pydantic 验证链，是唯一的裸入口字符串——
        取 basename（防目录注入）+ 过 NUL/孤立代理清洗闸 + 截 200（_doc_json 会回显给前端）。
        批量场景下"不支持的类型"只作逐项错误返回，不抛异常（一个坏文件不该拖垮整批）。
        """
        name = _clean_text(Path(file.filename or "unnamed").name)[:200]
        raw = file.file.read()
        if not supported_ext(name):
            return None, name, f"暂不支持的文件类型：{name}"
        doc = Document(tenant_id="default", kb_id=kb.id, name=name, status="pending",
                       size_bytes=len(raw), mime=file.content_type)
        session.add(doc)
        session.flush()
        p = Path(upload_dir) / f"{uuid4().hex[:8]}_{name}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        doc.storage_path = str(p)
        audit_record(session, "document_uploaded", user_email=admin.email,
                     target_type="document", target_id=doc.id,
                     detail={"kb_id": kb.id, "name": name}, ip=_client_ip(request))
        return doc, name, None

    @app.post("/api/v1/kb/{kb_id}/documents/batch", status_code=201,
              response_model=list[BatchUploadItemOut],
              responses={**_ERR(400, f"单批最多 {BATCH_MAX} 个文件"), **_ERR(404, "知识库不存在"),
                         **_ERR_BODY, **_ERR_GATE})
    def upload_documents_batch(kb_id: PathId, request: Request,
                               files: list[UploadFile] = File(...),
                               admin: User = Depends(require_license),
                               session: Session = Depends(get_session)):
        """批量上传（§A）：逐文件给出结果，**部分成功**——坏文件只在自己那行报错。

        与单文件端点的差异（有意）：不支持的扩展名不再整批 415，而是该行 error 字段。
        """
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        if len(files) > BATCH_MAX:
            raise HTTPException(400, f"单批最多 {BATCH_MAX} 个文件")
        stored: list[tuple[Document, bytes]] = []
        results: list[dict] = []
        for f in files:
            doc, name, err = _store_upload(session, kb, f, request, admin)
            if err or doc is None:
                results.append({"name": name, "document": None, "error": err})
                continue
            raw = _read_or_fail(session, doc)
            if raw is None:            # 读不出来：这一项以 failed 形态返回，其余照常
                results.append({"name": name, "document": _doc_json(doc), "error": None})
                continue
            stored.append((doc, raw))
            results.append({"name": name, "document": _doc_json(doc), "error": None})
        session.commit()
        if queue is not None:
            for doc, _raw in stored:
                queue.enqueue_import(doc.id)
            return results                       # pending，worker 逐个接手
        # 同步模式：逐个入库并把结果回填到对应项（坏文件已经在上面的 results 里带着 error）
        done: dict[int, dict] = {}
        for doc, raw in stored:
            doc = ingest_document(session, doc, raw, embedder=embedder, mineru=mineru,
                                  vision_fn=vision_fn,
                                  caption_images=cfg.effective()["doc_image_caption"],
                                  **_chunk_params(session, kb))
            done[doc.id] = _doc_json(doc)
        return [{**item,
                 "document": (done.get(item["document"]["id"], item["document"])
                              if item["document"] else None)}
                for item in results]

    @app.post("/api/v1/kb/{kb_id}/documents", status_code=201,
              response_model=DocOut,
              responses={**_ERR(404, "知识库不存在"), **_ERR(415, "不支持的文件类型"),
                         **_ERR_BODY, **_ERR_GATE})
    def upload_document(kb_id: PathId, request: Request, file: UploadFile = File(...),
                        admin: User = Depends(require_license),
                        session: Session = Depends(get_session)):
        kb = session.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        doc, name, err = _store_upload(session, kb, file, request, admin)
        if err:
            raise HTTPException(415, f"暂不支持的文件类型：{name}（一期 .txt/.md，MinerU 接入后支持 PDF/Office）")
        assert doc is not None
        session.commit()
        if queue is not None:
            queue.enqueue_import(doc.id)
            return _doc_json(doc)  # pending，worker 接手
        raw = _read_or_fail(session, doc)
        if raw is None:
            return _doc_json(doc)      # failed 带着原因回给前端，不是 500
        doc = ingest_document(session, doc, raw, embedder=embedder, mineru=mineru,
                              vision_fn=vision_fn,
                              caption_images=cfg.effective()["doc_image_caption"],
                              **_chunk_params(session, kb))
        return _doc_json(doc)

    @app.delete("/api/v1/documents/{doc_id}", status_code=204,
                responses={**_ERR(404, "文档不存在"), **_ERR_GATE})
    def delete_document(doc_id: PathId, request: Request,
                        admin: User = Depends(require_license),
                        session: Session = Depends(get_session)):
        """删文档：切块级联清、落盘原文件一并删（别把客户磁盘当垃圾场）。

        不可逆（要恢复得重新上传重建索引），故前端有二次确认；前端/接口的
        消息引用是历史快照，不随文档删除而变（当时的回答就该保持当时的出处）。
        """
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        name, kb_id, storage_path = doc.name, doc.kb_id, doc.storage_path
        session.delete(doc)          # chunks 走 FK CASCADE
        audit_record(session, "document_deleted", user_email=admin.email,
                     target_type="document", target_id=doc_id,
                     detail={"kb_id": kb_id, "name": name}, ip=_client_ip(request))
        session.commit()
        if storage_path:
            try:
                Path(storage_path).unlink(missing_ok=True)
            except OSError as exc:   # 文件删不掉不该让接口失败：DB 已一致，残留文件无害
                import logging

                logging.getLogger("umax").warning("原文件清理失败 %s：%s", storage_path, exc)

    def _visible_doc(session: Session, user: User, doc_id: int) -> Document:
        """按库级授权取文档：不可见（不存在/在未授权库）统一 404 同文案，探测不出差异。"""
        doc = session.get(Document, doc_id)
        allowed = allowed_kb_ids(session, user)
        if not doc or (allowed is not None and doc.kb_id not in allowed):
            raise HTTPException(404, "文档不存在")
        return doc

    @app.get("/api/v1/documents/{doc_id}", response_model=DocOut,
             responses={**_ERR(404, "文档不存在"), **_ERR_LOGIN_GATE})
    def get_document(doc_id: PathId, user: User = Depends(get_user),
                     session: Session = Depends(get_session)):
        return _doc_json(_visible_doc(session, user, doc_id))

    @app.get("/api/v1/documents/{doc_id}/chunks", response_model=list[ChunkPreviewOut],
             responses={**_ERR(404, "文档不存在"), **_ERR_LOGIN_GATE})
    def preview_chunks(doc_id: PathId, user: User = Depends(get_user),
                       session: Session = Depends(get_session)):
        _visible_doc(session, user, doc_id)
        return [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content,
                 "has_embedding": c.embedding is not None, "meta": c.meta or {}}
                for c in session.query(Chunk).filter_by(document_id=doc_id)
                .order_by(Chunk.chunk_index)]

    @app.patch("/api/v1/documents/{doc_id}", response_model=DocOut,
               responses={**_ERR(404, "文档不存在"), **_ERR_BODY, **_ERR_GATE})
    def patch_document(doc_id: PathId, body: DocPatchIn,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        # 有意不记审计（spec §5 / 任务 5 表）：状态机内部推进（worker 回写 pending/ready/failed），
        # 不是人工写操作——记了只会淹没真实操作事件
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        if body.status:
            doc.status = body.status
        doc.error = body.error
        session.commit()
        return _doc_json(doc)

    @app.post("/api/v1/documents/{doc_id}/reprocess", status_code=202, response_model=DocOut,
              responses={**_ERR(404, "文档不存在"), **_ERR_GATE})
    def reprocess(doc_id: PathId, request: Request,
                  admin: User = Depends(require_license),
                  session: Session = Depends(get_session)):
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        # 审计只 add 不 commit：队列分支随下面的 commit 落库，同步分支随 ingest_document
        # 内部的 commit 落库——两条路径都是"操作真发生了才有事件"
        audit_record(session, "document_reprocessed", user_email=admin.email,
                     target_type="document", target_id=doc.id, detail={"kb_id": doc.kb_id},
                     ip=_client_ip(request))
        if queue is not None:
            doc.status, doc.error = "pending", None
            session.commit()
            queue.enqueue_import(doc.id)
            return _doc_json(doc)
        raw = _read_or_fail(session, doc)
        if raw is None:
            return _doc_json(doc)
        doc = ingest_document(session, doc, raw, embedder=embedder,
                              mineru=mineru, vision_fn=vision_fn,
                              caption_images=cfg.effective()["doc_image_caption"],
                              **_chunk_params(session, session.get(KnowledgeBase, doc.kb_id)))
        return _doc_json(doc)

    # 配置中心请求体：按 SPEC 动态建模——单字段约束自动进 OpenAPI schema
    # （additionalProperties=false → 未知键 422；长度/范围由 Field 表达），
    # 前端与 fuzz 都拿得到真实约束，"约束没进 spec" 的坑不再重演。
    from typing import Optional as _Opt

    _fields: dict = {}
    for _key, _sp in cfg.spec.items():
        _t = {"float": float, "int": int, "bool": bool}.get(_sp.kind, str)
        _kw: dict = {"default": None, "description": _sp.label}
        if _sp.max_length is not None:
            _kw["max_length"] = _sp.max_length
        if _sp.minimum is not None:
            _kw["ge"] = _sp.minimum
        if _sp.maximum is not None:
            _kw["le"] = _sp.maximum
        _fields[_key] = (_Opt[_t], Field(**_kw))
    SettingsPutIn = pydantic_create_model("SettingsPutIn",
                                          __config__=ConfigDict(extra="forbid"), **_fields)

    @app.get("/api/v1/settings", response_model=SettingsSnapshotOut, responses={**_ERR_GATE})
    def get_settings_api(admin: User = Depends(require_admin_role)):
        return cfg.snapshot()

    @app.put("/api/v1/settings", response_model=SettingsSnapshotOut,
             responses={**_ERR_GATE, **_ERR_BODY})
    def put_settings_api(body: SettingsPutIn, request: Request,
                         admin: User = Depends(require_license),
                         session: Session = Depends(get_session)):
        # 只处理客户端真正带上的字段（model_fields_set）：缺席=不动，显式 null=回默认
        changes = {k: getattr(body, k) for k in body.model_fields_set}
        if changes:
            changed = cfg.put(session, changes, admin.email)
            if changed:
                audit_record(session, "settings_updated", user_email=admin.email,
                             target_type="settings", detail={"fields": sorted(changed)},
                             ip=_client_ip(request))
                session.commit()
        return cfg.snapshot()

    # ---- 用户级配额（§C：AI 要花钱，得有闸门）----
    def _period_used(session: Session, email: str, since: datetime) -> int:
        total = session.query(
            func.coalesce(func.sum(UsageRecord.prompt_tokens + UsageRecord.completion_tokens), 0)
        ).filter(UsageRecord.user_email == email, UsageRecord.created_at >= since).scalar()
        return int(total or 0)

    def _quota_state(session: Session, user: User) -> dict:
        """当日/当月已用 token 与限额。窗口按 UTC 自然日/自然月（与台账口径一致）。"""
        now = _now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        daily, monthly = (_period_used(session, user.email, day_start),
                          _period_used(session, user.email, month_start))
        ratio = float(cfg.effective()["quota_warn_ratio"])
        d_lim, m_lim = user.daily_token_limit, user.monthly_token_limit
        exceeded = (d_lim is not None and daily >= d_lim) or (m_lim is not None and monthly >= m_lim)
        near = (not exceeded) and (
            (d_lim is not None and d_lim > 0 and daily >= d_lim * ratio)
            or (m_lim is not None and m_lim > 0 and monthly >= m_lim * ratio))
        return {"daily_used": daily, "daily_limit": d_lim,
                "monthly_used": monthly, "monthly_limit": m_lim,
                "near_limit": near, "exceeded": exceeded, "warn_ratio": ratio}

    def _enforce_quota(session: Session, user: User) -> None:
        """超限即拦（429），且拦在检索与调模型之前——闸门的意义是不白花钱。"""
        st = _quota_state(session, user)
        if st["exceeded"]:
            if st["daily_limit"] is not None and st["daily_used"] >= st["daily_limit"]:
                raise HTTPException(429, f"今日 token 配额已用尽（{st['daily_used']}/"
                                         f"{st['daily_limit']}），请明日再试或联系管理员调整")
            raise HTTPException(429, f"本月 token 配额已用尽（{st['monthly_used']}/"
                                     f"{st['monthly_limit']}），请联系管理员调整")

    @app.get("/api/v1/usage/me", response_model=QuotaOut, responses={**_ERR_LOGIN_GATE})
    def usage_me(user: User = Depends(get_user), session: Session = Depends(get_session)):
        return _quota_state(session, user)

    @app.get("/api/v1/usage/users", response_model=list[UsageUserOut], responses={**_ERR_GATE})
    def usage_users(admin: User = Depends(require_admin_role),
                    session: Session = Depends(get_session)):
        """按人看用量与限额（"谁快超了"的一眼视图；被拦状态由 exceeded 字段承载，不写审计避免刷屏）。"""
        rows = []
        for u in session.query(User).order_by(User.id):
            rows.append({"id": u.id, "email": u.email, "name": u.name,
                         **_quota_state(session, u)})
        return rows

    def _chunk_params(session: Session, kb: KnowledgeBase | None) -> dict:
        """切块参数：一律走配置中心那份单一事实源（sync/async 必须同口径）。"""
        return cfg.chunk_params(kb)

    # ---- 检索与问答：可见性在检索层钳制（SQL 谓词），命中集就是授权集的子集 ----
    @app.post("/api/v1/retrieve", response_model=list[HitOut], responses={**_ERR_BODY, **_ERR_GATE})
    def retrieve_api(body: RetrieveIn, user: User = Depends(get_user),
                     session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        _guard_kb_ids(allowed, body.kb_ids)
        c = cfg.effective()
        return retrieve(session, body.query, embedder=embedder, kb_ids=body.kb_ids,
                        allowed_kb_ids=allowed, rerank=rerank,
                        recall_k=c["recall_k"], top_k=body.top_k or c["rerank_top_n"],
                        min_sim=c["min_sim"])

    @app.post("/api/v1/chat", response_model=ChatOut,
              responses={**_ERR(404, "会话不存在"), **_ERR(429, "配额已用尽"), **_ERR_BODY, **_ERR_GATE})
    def chat(body: ChatIn, user: User = Depends(get_user),
             session: Session = Depends(get_session)):
        allowed = allowed_kb_ids(session, user)
        _guard_kb_ids(allowed, body.kb_ids)
        c = cfg.effective()   # 每请求现读配置：后台改检索参数/提示词即时生效
        _enforce_quota(session, user)   # 配额闸门：超限在检索/调模型之前拦下
        # 传图提问（阶段 2）：先校验图片，再由 vision 模型转文字描述，拼进检索与生成的问题
        images = body.images or []
        if len(images) > IMAGE_COUNT_MAX:
            raise HTTPException(400, f"最多附带 {IMAGE_COUNT_MAX} 张图片")
        for u in images:
            if not LOGO_RE.match(u):
                raise HTTPException(400, "图片仅支持 data:image/* base64")
            if len(u) > IMAGE_MAX:
                raise HTTPException(400, "单张图片过大（≤2MB）")
        captions: list[str] = []
        for u in images:
            if vision_fn is None:
                break   # 未配视觉模型：文字照常答（图片仍随消息存储可回看）
            try:
                out = vision_fn(u)
            except Exception as exc:
                import logging
                logging.getLogger("umax").warning("vision 描述失败，跳过该图：%s", exc)
                continue
            captions.append(out["caption"])
            session.add(UsageRecord(tenant_id="default", user_email=user.email,
                                    scenario="vision", model=out.get("model") or s.chat_model,
                                    prompt_tokens=out.get("prompt_tokens") or 0,
                                    completion_tokens=out.get("completion_tokens") or 0,
                                    latency_ms=out.get("latency_ms")))
        query = body.question
        if captions:
            query += "\n\n【随问图片描述】\n" + "\n".join(captions)
        hits = retrieve(session, query, embedder=embedder, kb_ids=body.kb_ids,
                        allowed_kb_ids=allowed, rerank=rerank,
                        recall_k=c["recall_k"], top_k=c["rerank_top_n"], min_sim=c["min_sim"])
        # 终审收口①：给了 id 就必须"存在且归调用者"——不存在与跨用户同文案（同 list_messages），
        # 否则"不存在→新建回 200 / 别人的→404"成了会话存在性探测口（spec §0：不可区分）。
        # 只有 conversation_id 缺席（null）才新建会话。
        conv = session.get(Conversation, body.conversation_id) if body.conversation_id is not None else None
        if body.conversation_id is not None and (conv is None or conv.user_email != user.email):
            raise HTTPException(404, "会话不存在")
        if conv is None:
            conv = Conversation(tenant_id="default", user_email=user.email,
                                title=body.question[:32], kb_ids=body.kb_ids or [])
            session.add(conv)
            session.flush()
        user_parts: list[dict] = [{"type": "text", "text": body.question}]
        user_parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="user",
                            content=user_parts))

        if not hits or chat_fn is None:
            answer, usage, citations = c["chat_miss_answer"], {"prompt_tokens": 0, "completion_tokens": 0}, []
        else:
            try:
                out = chat_fn(query, hits)
            # 模型在两次问答之间被停用/删光/全挂：不把 500 甩用户脸上，转未命中兜底
            except Exception as exc:
                import logging
                logging.getLogger("umax").warning("chat 生成失败，转未命中兜底：%s", exc)
                out = None
            if out is None:
                answer, usage, citations = c["chat_miss_answer"], {"prompt_tokens": 0, "completion_tokens": 0}, []
            else:
                answer = clean_text(out["answer"])   # 模型输出也过规范化闸（否则 NUL 直接 500）
                usage = {"prompt_tokens": out.get("prompt_tokens") or 0,
                         "completion_tokens": out.get("completion_tokens") or 0}
                citations = [{"n": i, "doc_name": h["doc_name"], "chunk_id": h["id"],
                              "excerpt": h["content"][:80]} for i, h in enumerate(hits, 1)]
                # 记账单点在端点：网关 make_chat_fn 已交回记账职责（logged 约定退役），
                # 这里必记且只记一次，台账归真实登录者而非占位邮箱
                session.add(UsageRecord(tenant_id="default", user_email=user.email,
                                        scenario="chat", model=out.get("model") or s.chat_model,
                                        prompt_tokens=usage["prompt_tokens"],
                                        completion_tokens=usage["completion_tokens"],
                                        latency_ms=out.get("latency_ms")))
        session.add(Message(tenant_id="default", conversation_id=conv.id, role="assistant",
                            content=[{"type": "text", "text": answer}], citations=citations))
        session.commit()
        return {"conversation_id": conv.id, "answer": answer, "citations": citations,
                "cited_docs": parse_citations(answer, hits), "usage": usage}

    # ---- 会话历史：按登录者隔离（admin 也没有特权看别人的会话）----
    @app.get("/api/v1/conversations", response_model=list[ConversationOut], responses={**_ERR_LOGIN_GATE})
    def list_conversations(q: Annotated[str | None, Query(max_length=64)] = None,
                           user: User = Depends(get_user),
                           session: Session = Depends(get_session)):
        # 会话搜索（backlog）：标题子串匹配，仅本人会话；q 里的 LIKE 通配符按字面剔除
        query = session.query(Conversation).filter(Conversation.user_email == user.email)
        if q:
            literal = q.replace("%", "").replace("_", "").replace("\\", "")
            if not literal:
                return []   # 纯通配符按字面语义＝无标题含这些字符
            query = query.filter(Conversation.title.ilike(f"%{literal}%"))
        return [{"id": c.id, "title": c.title, "kb_ids": c.kb_ids}
                for c in query.order_by(Conversation.id)]

    @app.delete("/api/v1/conversations/{conv_id}", status_code=204,
                responses={**_ERR(404, "会话不存在"), **_ERR_LOGIN_GATE})
    def delete_conversation(conv_id: PathId, user: User = Depends(get_user),
                            session: Session = Depends(get_session)):
        conv = session.get(Conversation, conv_id)
        if not conv or conv.user_email != user.email:
            raise HTTPException(404, "会话不存在")
        session.delete(conv)   # messages 走 FK CASCADE；自助数据管理不进审计流
        session.commit()

    @app.get("/api/v1/conversations/{conv_id}/messages", response_model=list[MessageOut],
             responses={**_ERR(404, "会话不存在"), **_ERR_LOGIN_GATE})
    def list_messages(conv_id: PathId, user: User = Depends(get_user),
                      session: Session = Depends(get_session)):
        conv = session.get(Conversation, conv_id)
        if not conv or conv.user_email != user.email:
            raise HTTPException(404, "会话不存在")
        return [{"id": m.id, "role": m.role, "content": m.content, "citations": m.citations}
                for m in session.query(Message).filter_by(conversation_id=conv_id)
                .order_by(Message.id)]

    # ---- 模型后台（§C：改表即生效；key 加密存储、打码、不回传明文）----
    def _require_secret() -> str:
        if not gateway_secret:
            raise HTTPException(503, "未配置主密钥 GATEWAY_SECRET，无法管理模型 key")
        return gateway_secret

    def _model_json(m: ModelConfig) -> dict:
        plain = decrypt_secret(m.encrypted_api_key, _require_secret())
        return {"id": m.id, "scenario": m.scenario, "provider": m.provider,
                "base_url": m.base_url, "model_name": m.model_name,
                "capabilities": m.capabilities, "is_default": m.is_default,
                "fallback_rank": m.fallback_rank, "enabled": m.enabled,
                "api_key_masked": ("****" + plain[-4:]) if plain else ""}

    @app.post("/api/v1/models", status_code=201, response_model=ModelOut,
              responses={**_ERR(400, "scenario 非法"), **_ERR(503, "未配置 GATEWAY_SECRET"),
                         **_ERR_GATE})
    def create_model(body: ModelIn, request: Request,
                     admin: User = Depends(require_license),
                     session: Session = Depends(get_session)):
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, f"scenario 仅支持 {'/'.join(sorted(SCENARIOS))}")
        m = ModelConfig(tenant_id="default", scenario=body.scenario, provider=body.provider,
                        base_url=body.base_url, model_name=body.model_name,
                        encrypted_api_key=encrypt_secret(body.api_key, _require_secret()),
                        capabilities=body.capabilities, is_default=body.is_default,
                        fallback_rank=body.fallback_rank, enabled=body.enabled)
        session.add(m)
        session.flush()
        # detail 只进非敏感定位字段：api_key/base_url/provider 明文一律不入审计
        audit_record(session, "model_created", user_email=admin.email,
                     target_type="model", target_id=m.id,
                     detail={"scenario": body.scenario, "model_name": body.model_name,
                             "fallback_rank": body.fallback_rank}, ip=_client_ip(request))
        session.commit()
        return _model_json(m)

    @app.get("/api/v1/models", response_model=list[ModelOut], responses={**_ERR_GATE})
    def list_models(admin: User = Depends(require_admin_role), session: Session = Depends(get_session)):
        rows = session.query(ModelConfig).order_by(ModelConfig.scenario,
                                                   ModelConfig.fallback_rank, ModelConfig.id)
        return [_model_json(m) for m in rows]

    @app.patch("/api/v1/models/{model_id}", response_model=ModelOut,
               responses={**_ERR(400, "scenario 非法"), **_ERR(404, "模型配置不存在"),
                          **_ERR(503, "未配置 GATEWAY_SECRET"), **_ERR_GATE})
    def patch_model(model_id: PathId, body: ModelPatchIn, request: Request,
                    admin: User = Depends(require_license),
                    session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if not m:
            raise HTTPException(404, "模型配置不存在")
        data = body.model_dump(exclude_none=True)
        # detail 只进字段名（调用方请求改哪些字段），值一律不落——尤其 api_key 明文
        fields = sorted(data.keys())
        if "scenario" in data and data["scenario"] not in SCENARIOS:
            raise HTTPException(400, f"scenario 仅支持 {'/'.join(sorted(SCENARIOS))}")
        if "api_key" in data:
            data["encrypted_api_key"] = encrypt_secret(data.pop("api_key"), _require_secret())
        for k, v in data.items():
            setattr(m, k, v)
        if fields:   # 无变更不记审计（评审收编⑧/⑩ 统一裁定）：空 PATCH 是"什么都没改"，
            # 记一条 fields=[] 的事件只会污染事件流——与 patch_user 的 `if changed:` 同一口径
            audit_record(session, "model_updated", user_email=admin.email,
                         target_type="model", target_id=m.id, detail={"fields": fields},
                         ip=_client_ip(request))
        session.commit()
        return _model_json(m)

    @app.delete("/api/v1/models/{model_id}", status_code=204,
                responses={**_ERR(503, "未配置 GATEWAY_SECRET"), **_ERR_GATE})
    def delete_model(model_id: PathId, request: Request,
                     admin: User = Depends(require_license),
                     session: Session = Depends(get_session)):
        m = session.get(ModelConfig, model_id)
        if m:
            model_name = m.model_name      # 删前取：行没了就查不到被删的是哪个模型
            session.delete(m)
            audit_record(session, "model_deleted", user_email=admin.email,
                         target_type="model", target_id=model_id,
                         detail={"model_name": model_name}, ip=_client_ip(request))
            session.commit()

    # ---- 用量看板简版（§C：成本折算的事实来源）----
    @app.get("/api/v1/usage/summary", response_model=list[UsageSummaryOut], responses={**_ERR_GATE})
    def usage_summary(admin: User = Depends(require_admin_role),
                      session: Session = Depends(get_session)):
        rows = (session.query(UsageRecord.scenario, UsageRecord.model,
                              func.count().label("calls"),
                              func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
                              func.coalesce(func.sum(UsageRecord.completion_tokens), 0))
                .group_by(UsageRecord.scenario, UsageRecord.model).all())
        return [{"scenario": r[0], "model": r[1], "calls": r[2],
                 "prompt_tokens": int(r[3]), "completion_tokens": int(r[4])} for r in rows]

    # ---- 开放 API（§2.2）：API key 管理（admin 面）+ OpenAI 兼容端点（Bearer key）----
    # 兼容端点不走会话（客户系统没有浏览器 cookie），401/429/400 声明在该端点自己名下；
    # 契约守卫为它开 Bearer 例外面（见 test_contract_declared）。
    def _bearer_key(request: Request, session: Session) -> ApiKey:
        auth = request.headers.get("authorization") or ""
        raw = auth[7:] if auth.startswith("Bearer ") else ""
        row = (session.query(ApiKey).filter_by(key_hash=token_digest(raw)).first()
               if raw else None)
        if not row or not row.enabled:
            raise HTTPException(401, "无效的 API key")
        return row

    def _api_key_json(k: ApiKey) -> dict:
        return {"id": k.id, "name": k.name, "key_prefix": k.key_prefix, "kb_ids": k.kb_ids,
                "monthly_token_quota": k.monthly_token_quota, "enabled": k.enabled,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "created_at": k.created_at.isoformat()}

    def _valid_kb_ids(session: Session, kb_ids: list[int] | None) -> list[int] | None:
        if kb_ids is None:
            return None
        ids = sorted(set(kb_ids))
        if ids and len(session.query(KnowledgeBase.id)
                      .filter(KnowledgeBase.id.in_(ids)).all()) != len(ids):
            raise HTTPException(400, "存在不存在的知识库 id")
        return ids

    def _month_used_tokens(session: Session, key_id: int) -> int:
        month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = session.query(
            func.sum(UsageRecord.prompt_tokens + UsageRecord.completion_tokens)).filter(
            UsageRecord.user_email == f"apikey:{key_id}",
            UsageRecord.created_at >= month_start).scalar()
        return int(total or 0)

    @app.post("/api/v1/api-keys", status_code=201, response_model=ApiKeyCreatedOut,
              responses={**_ERR(400, "存在不存在的知识库 id"), **_ERR_GATE, **_ERR_BODY})
    def create_api_key(body: ApiKeyIn, request: Request,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        ids = _valid_kb_ids(session, body.kb_ids)
        plain, digest = new_api_key()
        k = ApiKey(tenant_id="default", name=body.name, key_hash=digest, key_prefix=plain[:13],
                   kb_ids=ids, monthly_token_quota=body.monthly_token_quota, created_by=admin.id)
        session.add(k)
        session.flush()
        # key 明文/摘要一律不入审计
        audit_record(session, "api_key_created", user_email=admin.email,
                     target_type="api_key", target_id=k.id,
                     detail={"name": body.name, "kb_ids": ids,
                             "monthly_token_quota": body.monthly_token_quota},
                     ip=_client_ip(request))
        session.commit()
        out = _api_key_json(k)
        out["key"] = plain   # 明文只在这一次响应里出现
        return out

    @app.get("/api/v1/api-keys", response_model=list[ApiKeyOut], responses={**_ERR_GATE})
    def list_api_keys(admin: User = Depends(require_admin_role),
                      session: Session = Depends(get_session)):
        return [_api_key_json(k) for k in session.query(ApiKey).order_by(ApiKey.id)]

    @app.patch("/api/v1/api-keys/{key_id}", response_model=ApiKeyOut,
               responses={**_ERR(400, "存在不存在的知识库 id"), **_ERR(404, "API key 不存在"),
                          **_ERR_GATE, **_ERR_BODY})
    def patch_api_key(key_id: PathId, body: ApiKeyPatchIn, request: Request,
                      admin: User = Depends(require_license),
                      session: Session = Depends(get_session)):
        k = session.get(ApiKey, key_id)
        if not k:
            raise HTTPException(404, "API key 不存在")
        fields: list[str] = []
        if body.name is not None and body.name != k.name:
            k.name = body.name
            fields.append("name")
        if body.kb_ids is not None:
            k.kb_ids = _valid_kb_ids(session, body.kb_ids)
            fields.append("kb_ids")
        if body.monthly_token_quota is not None:
            k.monthly_token_quota = body.monthly_token_quota
            fields.append("monthly_token_quota")
        if body.enabled is not None and body.enabled != k.enabled:
            k.enabled = body.enabled
            fields.append("enabled")
        if fields:
            audit_record(session, "api_key_updated", user_email=admin.email,
                         target_type="api_key", target_id=k.id,
                         detail={"fields": fields}, ip=_client_ip(request))
            session.commit()
        return _api_key_json(k)

    @app.delete("/api/v1/api-keys/{key_id}", status_code=204,
                responses={**_ERR(404, "API key 不存在"), **_ERR_GATE})
    def delete_api_key(key_id: PathId, request: Request,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        k = session.get(ApiKey, key_id)
        if not k:
            raise HTTPException(404, "API key 不存在")
        name = k.name   # 删前取：行没了就查不到被删的是哪枚
        session.delete(k)
        audit_record(session, "api_key_deleted", user_email=admin.email,
                     target_type="api_key", target_id=key_id,
                     detail={"name": name}, ip=_client_ip(request))
        session.commit()

    @app.post("/api/v1/openai/chat/completions", response_model=OpenAiChatOut,
              responses={**_ERR(400, "messages 里没有 user 消息"), **_ERR(401, "无效的 API key"),
                         **_ERR(429, "本月配额已用尽")})
    def openai_chat_completions(body: OpenAiChatIn, request: Request,
                                session: Session = Depends(get_session)):
        k = _bearer_key(request, session)
        if k.monthly_token_quota is not None and _month_used_tokens(session, k.id) >= k.monthly_token_quota:
            raise HTTPException(429, "本月配额已用尽")
        question = next((m.content for m in reversed(body.messages) if m.role == "user"), None)
        if not question:
            raise HTTPException(400, "messages 里没有 user 消息")
        # 作用域钳制在检索层（同 user_kb_grants 方向）：kb_ids=NULL 全库，数组=限定库
        allowed = set(k.kb_ids) if k.kb_ids is not None else None
        c = cfg.effective()
        hits = retrieve(session, question, embedder=embedder, kb_ids=None,
                        allowed_kb_ids=allowed, rerank=rerank, recall_k=c["recall_k"],
                        top_k=c["rerank_top_n"], min_sim=c["min_sim"])
        if not hits or chat_fn is None:
            answer, usage, citations = c["chat_miss_answer"], {"prompt_tokens": 0, "completion_tokens": 0}, []
        else:
            out = chat_fn(question, hits)
            answer = clean_text(out["answer"])   # 模型输出也过规范化闸（否则 NUL 直接 500）
            usage = {"prompt_tokens": out.get("prompt_tokens") or 0,
                     "completion_tokens": out.get("completion_tokens") or 0}
            citations = [{"n": i, "doc_name": h["doc_name"], "chunk_id": h["id"],
                          "excerpt": h["content"][:80]} for i, h in enumerate(hits, 1)]
            # 台账"谁"维度：apikey:{id}（配额聚合同口径）；model 取请求声明，缺省回默认
            session.add(UsageRecord(tenant_id="default", user_email=f"apikey:{k.id}",
                                    scenario="chat", model=body.model or s.chat_model,
                                    prompt_tokens=usage["prompt_tokens"],
                                    completion_tokens=usage["completion_tokens"],
                                    latency_ms=out.get("latency_ms")))
        k.last_used_at = _now()
        session.commit()
        return {"id": f"chatcmpl-{uuid4().hex[:12]}", "object": "chat.completion",
                "created": int(_now().timestamp()), "model": body.model or s.chat_model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": usage["prompt_tokens"],
                          "completion_tokens": usage["completion_tokens"],
                          "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"]},
                "citations": citations}   # 本产品扩展字段：OpenAI 没有但企业客户要溯源

    # ---- 白标（§2.2）：品牌名+logo 后台可改。GET 匿名可读（登录页首屏要显品牌，
    # 与 /health 同属匿名面）；PUT admin 独占全审计。app_settings 是通用键值表，
    # 后续提示词/检索参数后台化都走它，白标只是第一个租户。
    DEFAULT_BRAND_NAME = "Umax RAG"
    LOGO_RE = re.compile(r"^data:image/(?:png|jpeg|jpg|webp|gif|svg\+xml);base64,[A-Za-z0-9+/=]+$")
    LOGO_MAX = 400_000   # base64 字符数上限 ≈ 300KB 二进制，防把 DB 当图床

    def _setting_get(session: Session, key: str, default) -> object:
        row = session.get(AppSetting, key)
        return row.value if row else default

    def _setting_put(session: Session, key: str, value, by: str) -> None:
        row = session.get(AppSetting, key)
        if row:
            row.value, row.updated_by = value, by
        else:
            session.add(AppSetting(key=key, value=value, updated_by=by))

    def _branding_json(session: Session) -> dict:
        return {"brand_name": _setting_get(session, "brand_name", DEFAULT_BRAND_NAME),
                "logo": _setting_get(session, "logo", None)}

    @app.get("/api/v1/branding", response_model=BrandingOut)
    def get_branding(session: Session = Depends(get_session)):
        return _branding_json(session)

    @app.put("/api/v1/branding", response_model=BrandingOut,
             responses={**_ERR(400, "logo 仅支持 data:image/* base64（≤400KB）"),
                        **_ERR_GATE, **_ERR_BODY})
    def put_branding(body: BrandingPut, request: Request,
                     admin: User = Depends(require_license),
                     session: Session = Depends(get_session)):
        # 先全量校验再落库：logo 非法时 brand_name 也不能写进去（部分更新原子性）
        if "logo" in body.model_fields_set and body.logo is not None and (
                not LOGO_RE.match(body.logo) or len(body.logo) > LOGO_MAX):
            raise HTTPException(400, "logo 仅支持 data:image/* base64（≤400KB）")
        fields = []
        if body.brand_name is not None:
            _setting_put(session, "brand_name", body.brand_name.strip(), admin.email)
            fields.append("brand_name")
        if "logo" in body.model_fields_set:
            _setting_put(session, "logo", body.logo, admin.email)
            fields.append("logo")
        if fields:
            # detail 只记字段名：logo 的 base64 数据体不入审计（肥且无排查价值）
            audit_record(session, "branding_updated", user_email=admin.email,
                         target_type="branding", detail={"fields": fields},
                         ip=_client_ip(request))
            session.commit()
        return _branding_json(session)

    # ==================== 评测（§阶段2「评测体系正式化」）====================
    # 为什么值得进产品而不是留个脚本：客户问"你凭什么叫企业级、凭什么说更准"时，
    # 需要一份能反复跑、能看历史、数字可比的东西。脚本做不到"改完参数立刻看有没有退化"。
    def _question_json(q: EvalQuestion) -> dict:
        return {"id": q.id, "question": q.question, "expect_all": q.expect_all or [],
                "expect_any": q.expect_any or [], "cites": q.cites or [],
                "category": q.category or "", "note": q.note, "enabled": bool(q.enabled),
                "created_at": q.created_at}

    def _run_json(r: EvalRun) -> dict:
        return {"id": r.id, "status": r.status, "total": r.total, "passed": r.passed,
                "metrics": r.metrics or {}, "kb_ids": r.kb_ids, "judge": bool(r.judge),
                "chat_model": r.chat_model, "embedding_model": r.embedding_model,
                "error": r.error, "created_by": r.created_by,
                "started_at": r.started_at, "finished_at": r.finished_at}

    def _item_json(i: EvalItemResult) -> dict:
        return {"id": i.id, "question_id": i.question_id, "question": i.question,
                "category": i.category or "", "note": i.note,
                "expect_all": i.expect_all or [], "expect_any": i.expect_any or [],
                "cites": i.cites or [], "answer": i.answer,
                "cited_docs": i.cited_docs or [], "top_docs": i.top_docs or [],
                "checks": i.checks or {}, "judge": i.judge, "passed": bool(i.passed),
                "rank": i.rank, "latency_ms": i.latency_ms, "error": i.error}

    def _kb_scope_text(session: Session, kb_ids: list[int] | None) -> str:
        if kb_ids is None:
            return "全部知识库"
        rows = (session.query(KnowledgeBase).filter(KnowledgeBase.id.in_(kb_ids))
                .order_by(KnowledgeBase.id).all()) if kb_ids else []
        # 库删了留孤儿 id（EvalRun.kb_ids 是 JSONB 无 FK）：报告要如实说"当时评的那个库没了"
        return "、".join(k.name for k in rows) or "已删除的知识库"

    def _execute_eval(run_id: int) -> None:
        """后台执行一轮评测。

        逐题走**与问答端点同一条链路**（同一 retrieve、同一 chat_fn、同一份配置快照）——
        否则评测证明的不是线上效果。每题单独落库 + 单独记账（评测是真花钱的调用），
        所以前端轮询能看到进度、进程被杀也只丢当前这一题。
        单题异常只记在该题身上（stage0 教训：一轮 17/20 里三题败在生成调用而非检索，
        报告必须能区分这两类）；只有整轮性异常才把 run 标 failed——绝不让前端永远转圈。
        """
        try:
            with Session(engine) as session:
                run = session.get(EvalRun, run_id)
                if run is None:
                    return
                c = cfg.effective()
                # 记账归属：单库评测记在该库上（用量视图按库看成本），多库/全库记 NULL
                usage_kb_id = run.kb_ids[0] if run.kb_ids and len(run.kb_ids) == 1 else None
                results: list[dict] = []
                used_models: set[str] = set()
                questions = (session.query(EvalQuestion).filter_by(enabled=True)
                             .order_by(EvalQuestion.id).all())
                for q in questions:
                    t0 = time.monotonic()
                    answer: str | None = None
                    cited_docs: list[str] = []
                    top_docs: list[str] = []
                    hits: list[dict] = []
                    err: str | None = None
                    try:
                        hits = retrieve(session, q.question, embedder=embedder,
                                        kb_ids=run.kb_ids, allowed_kb_ids=None, rerank=rerank,
                                        recall_k=c["recall_k"], top_k=c["rerank_top_n"],
                                        min_sim=c["min_sim"])
                        top_docs = [h["doc_name"] for h in hits]
                        if hits and chat_fn is not None:
                            out = chat_fn(q.question, hits)
                            answer = clean_text(out["answer"])   # 模型输出也过规范化闸（否则 NUL 直接 500）
                            cited_docs = parse_citations(answer, hits)
                            if out.get("model"):
                                used_models.add(out["model"])
                            session.add(UsageRecord(
                                tenant_id="default", user_email=run.created_by or "eval@local",
                                kb_id=usage_kb_id, scenario="chat",
                                model=out.get("model") or s.chat_model,
                                prompt_tokens=out.get("prompt_tokens") or 0,
                                completion_tokens=out.get("completion_tokens") or 0,
                                latency_ms=out.get("latency_ms")))
                        else:
                            # 没配模型或没命中：走未命中兜底并如实记为"没过"——不假装跑过模型
                            answer = c["chat_miss_answer"]
                    except Exception as exc:
                        err = f"{exc.__class__.__name__}: {exc}"
                    checks = check_item({"expect_all": q.expect_all, "expect_any": q.expect_any,
                                         "cites": q.cites},
                                        answer=answer, cited_docs=cited_docs, top_docs=top_docs)
                    # 裁判只判"真发生过生成"的题（有命中且有回答）：对未命中兜底话术打 faithfulness
                    # 没有意义（那句话本来就不是从资料里生成的），硬判只会往指标里灌噪声
                    verdict: dict | None = None
                    if run.judge and judge_fn is not None and hits and answer and err is None:
                        try:
                            verdict = judge_fn(q.question, answer, hits)
                            session.add(UsageRecord(
                                tenant_id="default", user_email=run.created_by or "eval@local",
                                kb_id=usage_kb_id, scenario="chat",
                                model=(verdict.get("model") or s.chat_model),
                                prompt_tokens=verdict.get("prompt_tokens") or 0,
                                completion_tokens=verdict.get("completion_tokens") or 0,
                                latency_ms=verdict.get("latency_ms")))
                        except Exception as exc:   # 裁判挂了不该让这道题的确定性判据失效
                            verdict = None
                            err = err or f"裁判调用失败：{exc.__class__.__name__}: {exc}"
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    session.add(EvalItemResult(
                        run_id=run.id, question_id=q.id, question=q.question,
                        category=q.category, note=q.note, expect_all=q.expect_all,
                        expect_any=q.expect_any, cites=q.cites, answer=answer,
                        cited_docs=cited_docs, top_docs=top_docs, checks=checks,
                        judge=verdict, passed=checks["passed"], rank=checks["rank"],
                        latency_ms=latency_ms, error=err))
                    results.append({**checks, "category": q.category, "cites": q.cites,
                                    "latency_ms": latency_ms, "judge": verdict})
                    run.total = len(results)
                    run.passed = sum(1 for r in results if r["passed"])
                    session.commit()
                metrics = aggregate(results)
                metrics["judge"] = judge_stats(results)
                run.metrics, run.total, run.passed = metrics, metrics["total"], metrics["passed"]
                run.chat_model = sorted(used_models)[0] if used_models else None
                run.embedding_model = s.embedding_model if embedder is not None else None
                run.status, run.finished_at = "done", _now()
                session.commit()
        except Exception as exc:   # 整轮性故障：连不上库、配置读炸等——留证据，别静默
            logging.getLogger("umax").exception("评测执行失败 run=%s", run_id)
            try:
                with Session(engine) as session:
                    run = session.get(EvalRun, run_id)
                    if run is not None:
                        run.status, run.error = "failed", f"{exc.__class__.__name__}: {exc}"
                        run.finished_at = _now()
                        session.commit()
            except Exception:      # 兜底失败就只能靠日志了，绝不把异常再抛进后台线程
                logging.getLogger("umax").exception("评测失败态回写也失败 run=%s", run_id)

    @app.get("/api/v1/eval/questions", response_model=list[EvalQuestionOut],
             responses={**_ERR_GATE})
    def list_eval_questions(admin: User = Depends(require_admin_role),
                            session: Session = Depends(get_session)):
        return [_question_json(q) for q in
                session.query(EvalQuestion).order_by(EvalQuestion.id)]

    @app.post("/api/v1/eval/questions", status_code=201, response_model=EvalQuestionOut,
              responses={**_ERR_BODY, **_ERR_GATE})
    def create_eval_question(body: EvalQuestionIn, request: Request,
                             admin: User = Depends(require_license),
                             session: Session = Depends(get_session)):
        q = EvalQuestion(tenant_id="default", question=body.question,
                         expect_all=body.expect_all, expect_any=body.expect_any,
                         cites=body.cites, category=body.category, note=body.note,
                         enabled=body.enabled)
        session.add(q)
        session.flush()
        audit_record(session, "eval_question_created", user_email=admin.email,
                     target_type="eval_question", target_id=q.id,
                     detail={"category": q.category}, ip=_client_ip(request))
        session.commit()
        return _question_json(q)

    @app.patch("/api/v1/eval/questions/{qid}", response_model=EvalQuestionOut,
               responses={**_ERR(404, "金标准题不存在"), **_ERR_BODY, **_ERR_GATE})
    def patch_eval_question(qid: PathId, body: EvalQuestionPatchIn, request: Request,
                            admin: User = Depends(require_license),
                            session: Session = Depends(get_session)):
        q = session.get(EvalQuestion, qid)
        if not q:
            raise HTTPException(404, "金标准题不存在")
        # 只处理真正带上的字段且非 null（缺席/显式 null 都=不动）：改标尺必须留痕
        fields = [k for k in body.model_fields_set if getattr(body, k) is not None]
        for k in fields:
            setattr(q, k, getattr(body, k))
        if fields:
            audit_record(session, "eval_question_updated", user_email=admin.email,
                         target_type="eval_question", target_id=q.id,
                         detail={"fields": sorted(fields)}, ip=_client_ip(request))
            session.commit()
        return _question_json(q)

    @app.delete("/api/v1/eval/questions/{qid}", status_code=204,
                responses={**_ERR(404, "金标准题不存在"), **_ERR_GATE})
    def delete_eval_question(qid: PathId, request: Request,
                             admin: User = Depends(require_license),
                             session: Session = Depends(get_session)):
        """删题：历史运行的单题明细**不删**（question_id 置 NULL，快照还在）——
        评测记录是"当时的证据"，拿今天的尺子重判昨天的答案就失去可比性了。"""
        q = session.get(EvalQuestion, qid)
        if not q:
            raise HTTPException(404, "金标准题不存在")
        session.delete(q)
        audit_record(session, "eval_question_deleted", user_email=admin.email,
                     target_type="eval_question", target_id=qid,
                     detail={"category": q.category}, ip=_client_ip(request))
        session.commit()

    @app.post("/api/v1/eval/runs", status_code=202, response_model=EvalRunOut,
              responses={**_ERR(400, "知识库 id 不存在、没有启用中的金标准题，或未配置裁判模型"),
                         **_ERR_BODY, **_ERR_GATE})
    def start_eval_run(body: EvalRunIn, request: Request,
                       admin: User = Depends(require_license),
                       session: Session = Depends(get_session)):
        """起一轮评测并**立即返回**：20 题真模型要一两分钟，占着请求等会让浏览器/反代超时。
        返回 202 + run（status=running），前端轮询 /eval/runs/{id} 看进度。"""
        kb_ids = _valid_kb_ids(session, body.kb_ids)   # 与开放 API 同一套库存在性校验
        if not session.query(EvalQuestion).filter_by(enabled=True).count():
            raise HTTPException(400, "没有启用中的金标准题：先到金标准集里添加或启用")
        if body.judge and judge_fn is None:
            raise HTTPException(400, "未配置裁判模型：先在模型页登记 chat 模型")
        run = EvalRun(tenant_id="default", status="running", kb_ids=kb_ids, judge=body.judge,
                      created_by=admin.email)
        session.add(run)
        session.flush()
        run_id = run.id
        audit_record(session, "eval_run_started", user_email=admin.email,
                     target_type="eval_run", target_id=run_id,
                     detail={"kb_ids": kb_ids}, ip=_client_ip(request))
        session.commit()
        spawn_fn(lambda: _execute_eval(run_id))
        session.refresh(run)     # 同步 spawn（测试）时已经跑完：回读真实状态再回给调用方
        return _run_json(run)

    @app.get("/api/v1/eval/runs", response_model=list[EvalRunOut], responses={**_ERR_GATE})
    def list_eval_runs(limit: QueryInt = 20, admin: User = Depends(require_admin_role),
                       session: Session = Depends(get_session)):
        # 上下界在端点内钳位（同 /audit：越界不是违法请求，不出 422 面）
        limit = max(1, min(limit, 100))
        return [_run_json(r) for r in session.query(EvalRun)
                .order_by(EvalRun.id.desc()).limit(limit)]

    @app.get("/api/v1/eval/runs/{run_id}", response_model=EvalRunDetailOut,
             responses={**_ERR(404, "评测记录不存在"), **_ERR_GATE})
    def get_eval_run(run_id: PathId, admin: User = Depends(require_admin_role),
                     session: Session = Depends(get_session)):
        run = session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(404, "评测记录不存在")
        items = (session.query(EvalItemResult).filter_by(run_id=run_id)
                 .order_by(EvalItemResult.id).all())
        return {**_run_json(run), "items": [_item_json(i) for i in items]}

    @app.get("/api/v1/eval/runs/{run_id}/report", response_model=EvalReportOut,
             responses={**_ERR(404, "评测记录不存在"), **_ERR_GATE})
    def eval_report(run_id: PathId, admin: User = Depends(require_admin_role),
                    session: Session = Depends(get_session)):
        """Markdown 报告（JSON 里带 markdown 字符串）：交付文档要能贴，两次跑要能逐行 diff。
        有意不做成下载端点——media-type 面越小，契约越好守（同 /license 的取值思路）。"""
        run = session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(404, "评测记录不存在")
        items = (session.query(EvalItemResult).filter_by(run_id=run_id)
                 .order_by(EvalItemResult.id).all())
        markdown = render_report(
            meta={"time": (run.finished_at or run.started_at or _now()).strftime("%Y-%m-%d %H:%M"),
                  "kb_scope": _kb_scope_text(session, run.kb_ids),
                  "chat_model": run.chat_model, "embedding_model": run.embedding_model},
            metrics=run.metrics or {}, items=[_item_json(i) for i in items])
        return {"markdown": markdown}

    @app.delete("/api/v1/eval/runs/{run_id}", status_code=204,
                responses={**_ERR(404, "评测记录不存在"), **_ERR_GATE})
    def delete_eval_run(run_id: PathId, request: Request,
                        admin: User = Depends(require_license),
                        session: Session = Depends(get_session)):
        # 删历史要审计：删掉的正是"更准"的证据，动作本身得留痕
        run = session.get(EvalRun, run_id)
        if not run:
            raise HTTPException(404, "评测记录不存在")
        session.delete(run)      # 单题明细走 FK CASCADE
        audit_record(session, "eval_run_deleted", user_email=admin.email,
                     target_type="eval_run", target_id=run_id,
                     detail={"passed": run.passed, "total": run.total}, ip=_client_ip(request))
        session.commit()

    # ==================== 重建索引（§3.3 风险对策「换 embedding 模型翻车」）====================
    def _execute_reindex(doc_ids: list[int]) -> None:
        """逐篇重建（各自独立 session 提交）。单篇失败只记在那篇身上——一篇坏文件不该让
        整库重建停在中途（与批量上传的"部分成功"同一口径）。整轮的异常只记日志：
        此时各篇状态已落库，前端看得到哪些成了、哪些没有。"""
        c = cfg.effective()
        for doc_id in doc_ids:
            try:
                with Session(engine) as session:
                    doc = session.get(Document, doc_id)
                    if doc is None:
                        continue         # 重建期间被删了：跳过，不是错误
                    kb = session.get(KnowledgeBase, doc.kb_id)
                    ingest_document(session, doc, read_stored(doc), embedder=embedder,
                                    mineru=mineru, vision_fn=vision_fn,
                                    caption_images=c["doc_image_caption"],
                                    **_chunk_params(session, kb))
            except Exception as exc:     # 文件丢了/解析器炸了：只影响这一篇
                logging.getLogger("umax").exception("重建索引失败 doc=%s", doc_id)
                try:
                    with Session(engine) as s2:
                        d2 = s2.get(Document, doc_id)
                        if d2 is not None and d2.status != "ready":
                            # 错误文案与 ingest_document 的失败口径一致（同一处代码路径产出的东西要同形）
                            d2.status = "failed"
                            d2.error = f"{exc.__class__.__name__}: {exc}"
                            s2.commit()
                except Exception:
                    logging.getLogger("umax").exception("重建失败态回写也失败 doc=%s", doc_id)

    @app.post("/api/v1/reindex", status_code=202, response_model=ReindexOut,
              responses={**_ERR(400, "知识库不存在，或范围内没有可重建的文档"),
                         **_ERR_BODY, **_ERR_GATE})
    def reindex(body: ReindexIn, request: Request,
                admin: User = Depends(require_license),
                session: Session = Depends(get_session)):
        """一键重建索引：范围内文档**全部重新解析/切块/向量化**。

        为什么需要它：换 embedding 模型后旧向量与新查询不可比，全库必须从头算一遍；
        客户"先传资料、后买 key"或中途换型都会撞上，一篇篇点「重试」不现实。
        **语义是"全量重来"而不是"只补缺失"**——可预测比省算力重要（按钮上也这么写），
        想只补缺失的少数文档，用单篇「重试」即可。

        必须后台跑：同步档下几十上百篇要几分钟，占着请求必然被浏览器/反代掐断。
        进度就靠文档自己的状态机（pending→parsing→ready/failed）——前端已在轮询它，不另造一套进度。
        """
        kb_ids = _valid_kb_ids(session, body.kb_ids)
        q = session.query(Document)
        if kb_ids is not None:
            q = q.filter(Document.kb_id.in_(kb_ids))
        docs = q.order_by(Document.id).all()
        if not docs:
            raise HTTPException(400, "没有可重建的文档：先上传文档")
        # 状态先翻 pending 再返回：前端轮询立刻看到"排队中"，也不会把正在重建的库误显示成"就绪"
        for d in docs:
            d.status, d.error = "pending", None
        doc_ids = [d.id for d in docs]
        single = kb_ids[0] if kb_ids and len(kb_ids) == 1 else None
        audit_record(session, "reindex_started", user_email=admin.email,
                     target_type="kb" if single else "reindex", target_id=single,
                     detail={"kb_ids": kb_ids, "documents": len(doc_ids)},
                     ip=_client_ip(request))
        session.commit()
        if queue is not None:
            for doc_id in doc_ids:       # 异步档交给 worker（同 reprocess 的口径）
                queue.enqueue_import(doc_id)
        else:
            spawn_fn(lambda: _execute_reindex(doc_ids))
        return {"documents": len(doc_ids), "kb_ids": kb_ids}

    app.add_middleware(_AllowHeaderMiddleware, routes=app.router.routes)
    return app


def build_production_app(upload_dir: str = "uploads",
                         engine: Engine | None = None) -> FastAPI:
    """真依赖装配：配了 GATEWAY_SECRET 且表里有对应场景模型 → 走网关（后台改表即生效）；
    否则退回 .env 里的百炼直连（阶段 0 链路，冒烟可跑）。"""
    from sqlalchemy import create_engine

    from app.db.base import Base
    from app.services.chat import ChatClient, make_chat_fn
    from app.services.embeddings import BailianEmbedder
    from app.services.parsers import MinerUClient
    from app.services.rerank import RerankClient, reorder

    s = get_settings()
    if engine is None:
        engine = create_engine(s.sqlalchemy_url(), pool_pre_ping=True)
        import app.models  # noqa: F401  一键部署：启动即建表（正式迁移方案后续以 Alembic 接管）
        ensure_vector_extension(engine)   # 必须先于建表：VECTOR 列需要扩展存在（真机踩过）
        Base.metadata.create_all(engine)
    # 老库一键升级的过渡 pragmatics（无 Alembic）：create_all 不给已存在的表加列，
    # users.name/status 是任务 2 新增列——幂等 ADD COLUMN IF NOT EXISTS 补齐（fresh 库同样通过）
    with engine.begin() as con:
        con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                            "name VARCHAR(128) NOT NULL DEFAULT ''"))
        con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                            "status VARCHAR(16) NOT NULL DEFAULT 'active'"))
        con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                            "must_change_password BOOLEAN NOT NULL DEFAULT FALSE"))
        con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_token_limit INTEGER"))
        con.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_token_limit INTEGER"))
        # 评测的裁判字段（§阶段2 Ragas 侧）：表是上一轮刚建的，加列同样走幂等 pragmatics——
        # 客户库里的历史运行不会因为加列而丢分（judge 为空即"未启用裁判"）
        con.execute(sa_text("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS judge BOOLEAN "
                            "NOT NULL DEFAULT FALSE"))
        con.execute(sa_text("ALTER TABLE eval_item_results ADD COLUMN IF NOT EXISTS judge JSONB"))
    # 播种初始管理员（spec §2）：users 空表时按 ADMIN_EMAIL/ADMIN_PASSWORD 落一条 role=admin，
    # 口令 hash 后入库（明文永不落库）。测试装配 create_app 不播种，走 conftest.seed_user——
    # 故这里只在 build_production_app 里做，且放在 engine 判定之后（注入 engine 也要播种）。
    with Session(engine) as ses:   # ses 而非 s：s 已是 Settings，with 目标名会覆盖函数作用域
        if ses.query(User).first() is None:
            ses.add(User(tenant_id="default", email=s.admin_email, name="admin",
                         hashed_password=hash_password(s.admin_password), role="admin",
                         must_change_password=True))   # 初始口令是模板口令，首登强改密（初始化向导第一屏）
            ses.commit()
            import logging
            logging.getLogger("umax").warning(
                "已播种初始管理员 %s（ADMIN_EMAIL/ADMIN_PASSWORD）——首次登录强制修改口令", s.admin_email)
        # 金标准集播种（§阶段2）：空表时灌入随应用打包的预置集（与 stage0 同源 20 题）。
        # "评测集第一天就建"这条风险对策要开箱即生效——让客户自己攒题，这件事一定被拖到永远。
        # 只判空表：客户删改过的题集不会被下次启动覆盖回去（预置集是起手牌，不是主人）。
        if ses.query(EvalQuestion).first() is None:
            gold = load_golden()
            if gold:
                ses.add_all([EvalQuestion(tenant_id="default", **row) for row in gold])
                ses.commit()
                import logging
                logging.getLogger("umax").info(
                    "已播种 %d 条预置金标准题（可在评测页删改）", len(gold))
    chat_fn = embedder = vision_fn = None
    judge_fn = None
    bailian_chat = bailian_embedder = None
    cfg_store = SettingsStore(engine, s)          # 配置中心：端点与生成层共用同一实例
    prompt_of = lambda key: (lambda: cfg_store.effective()[key])  # noqa: E731  每次调用现取
    bailian_client = None
    bailian_rerank = None
    if s.dashscope_api_key:
        # .env 百炼直连（阶段 0 链路）：作为网关表未配置时的开发/冒烟兜底
        bailian_client = ChatClient(api_key=s.dashscope_api_key,
                                    base_url=s.dashscope_compat_base, model=s.chat_model)
        bailian_chat = make_chat_fn(bailian_client,
                                    system_prompt=prompt_of("chat_system_prompt"))
        bailian_embedder = BailianEmbedder(api_key=s.dashscope_api_key,
                                           base_url=s.dashscope_compat_base,
                                           model=s.embedding_model,
                                           dimensions=s.embedding_dim)
        if s.rerank_model:   # 重排只在原生端点（兼容端点没有 /rerank，实测返回空）
            direct_rerank = RerankClient(api_key=s.dashscope_api_key,
                                         base_url=s.dashscope_native_base,
                                         model=s.rerank_model)

            def bailian_rerank(query: str, hits: list[dict]) -> list[dict]:
                """与网关侧同形的适配器。**记账口径也一致**（scenario=rerank、归检索链路）：
                重排是花钱的一步，无论走网关还是 .env 直连都必须进台账，否则"成本闸门"漏一边。
                """
                out = direct_rerank.rerank(query, [h.get("content", "") for h in hits],
                                           top_n=len(hits))
                with Session(engine) as ses:
                    ses.add(UsageRecord(tenant_id="default", user_email="system@local",
                                        kb_id=(hits[0].get("kb_id") if hits else None),
                                        scenario="rerank", model=s.rerank_model,
                                        prompt_tokens=out["prompt_tokens"],
                                        completion_tokens=0))
                    ses.commit()
                return reorder(hits, out["results"])
    gw = None
    if s.gateway_secret:
        from app.services.gateway import ModelGateway

        gw = ModelGateway(engine, secret=s.gateway_secret)
        # 运行时换模型即生效（§C）：网关 fn 每次调用现读表，启动时表空不再"判死"——
        # 后台登记第一个模型立即接线；表空且无百炼 → chat MISS / 入库 BM25-only 降级
        chat_fn = with_gateway_fallback(gw.make_chat_fn(system_prompt=prompt_of("chat_system_prompt")),
                                        bailian_chat)
        embedder = FallbackEmbedder(gw.make_embedder(), bailian_embedder)
        vision_fn = gw.make_vision_fn(vision_prompt=prompt_of("vision_prompt"))  # 未配 vision 场景则端点降级
    else:
        chat_fn, embedder = bailian_chat, bailian_embedder

    def _judge_complete(messages: list[dict]) -> dict:
        """裁判的通路：网关优先（后台换裁判模型即生效），表里没有 chat 模型时退 .env 直连。

        "配了但全挂"（GatewayError）**刻意不降级**——与问答同一口径：那是配置错误要修，
        静默换一家只会让"为什么分数变了"变成无解之谜。
        """
        from app.services.gateway import NoProviderError

        if gw is not None:
            try:
                return gw.chat(messages, log=False)
            except NoProviderError:
                if bailian_client is None:
                    raise
        if bailian_client is None:
            raise NoProviderError("没有可用的裁判通路（网关未配 chat 模型且未配 DASHSCOPE_API_KEY）")
        return bailian_client.complete(messages)

    judge_fn = make_judge_fn(_judge_complete) if (gw is not None or bailian_client is not None) else None

    # 精排（§3.2）：配了 rerank 模型才生效；.env 侧走**原生端点**（重排没有兼容格式）
    # 表里没有 rerank 模型 + 没有 .env 直连 → 两路皆空：装配一个恒等函数，
    # 让"没配重排"这件事在检索路径上表现为"原样返回候选"（而不是 None 把检索打断）
    rerank_fn = with_rerank_fallback(gw.make_rerank_fn() if gw is not None else None,
                                     bailian_rerank)
    mineru = MinerUClient(s.mineru_base_url) if s.mineru_base_url else None
    # 装配可见性（售后排查"后台配了模型为什么没生效"的第一手依据；测试也据此守护漏传）
    # 历史教训：vision_fn 曾在网关分支里算出来却没传进 create_app，视觉能力静默空转
    queue = None
    if s.queue_backend == "arq":
        from app.queue import ArqQueue

        queue = ArqQueue(redis_host=s.redis_host, redis_port=s.redis_port)
    if not s.admin_email or not s.admin_password:
        import logging

        logging.getLogger("umax").warning(
            "ADMIN_EMAIL/ADMIN_PASSWORD 未配置：将按默认账号播种初始管理员，部署后必须登录改密")
    app = create_app(engine=engine, embedder=embedder, chat_fn=chat_fn,
                     upload_dir=upload_dir, mineru=mineru, queue=queue,
                     vision_fn=vision_fn, license_public_key=s.license_public_key,
                     license_file=str(s.license_file), settings_store=cfg_store,
                     judge_fn=judge_fn, rerank_fn=rerank_fn)
    app.state.wired = {"chat": chat_fn is not None, "embedder": embedder is not None,
                       "vision": vision_fn is not None, "mineru": mineru is not None,
                       "queue": queue is not None, "judge": judge_fn is not None,
                       "rerank": rerank_fn is not None,
                       "license_enforced": bool(s.license_public_key)}
    return app


def main() -> None:
    import os

    import uvicorn

    # 容器内必须绑 0.0.0.0 才能被反代/compose 网络访问；本机开发默认 127.0.0.1 不变
    uvicorn.run(build_production_app(), host=os.environ.get("UVICORN_HOST", "127.0.0.1"),
                port=int(os.environ.get("UVICORN_PORT", "8000")))


if __name__ == "__main__":
    main()
