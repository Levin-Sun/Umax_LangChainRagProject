# 核心数据表（需求文档 §3.3）——messages.content 结构化多模态，全表预留 tenant_id
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.config import get_settings
from app.db.base import Base

T = lambda: Column(String(64), nullable=False, server_default="default", index=True)  # noqa: E731
J = lambda **kw: Column(JSONB, **kw)  # noqa: E731


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    email = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(128), nullable=False, default="", server_default="")
    status = Column(String(16), nullable=False, default="active", server_default="active")  # active/disabled
    role = Column(String(16), nullable=False, default="member")  # admin / member
    # 首登强改密门闸：口令非本人设定（播种/管理员代发代重置）即置位，本人经 /auth/change-password 解除
    must_change_password = Column(Boolean, nullable=False, default=False, server_default="false")
    # 用户级成本闸门（§C）：日/月 token 上限，NULL=不限；达到即拦在调模型之前（usage_records 聚合）
    daily_token_limit = Column(Integer)
    monthly_token_limit = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    name = Column(String(128), nullable=False)
    description = Column(Text)
    # 建库时锁定 embedding 模型与切分参数（重建索引提醒见风险表）
    embedding_model = Column(String(128))
    chunk_target = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                   nullable=False, index=True)
    name = Column(String(512), nullable=False)
    # 解析流水线状态机：pending → parsing → ready / failed（失败可重试回 pending）
    status = Column(String(16), nullable=False, default="pending", index=True)
    error = Column(Text)
    storage_path = Column(String(1024))  # 原始文件落盘路径（重处理/MinerU 解析用）
    size_bytes = Column(Integer)
    mime = Column(String(128))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                   nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    meta = J(nullable=False, default=dict)  # 页码/表格坐标/图描述来源等
    acl = J()  # NULL=库内全员可见；二期检索层权限过滤用
    embedding = Column(Vector(get_settings().embedding_dim))


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    scenario = Column(String(16), nullable=False, index=True)  # chat/embedding/rerank/vision
    provider = Column(String(32), nullable=False)
    base_url = Column(Text, nullable=False)
    encrypted_api_key = Column(Text, nullable=False)  # 主密钥在 env，界面打码不回传
    model_name = Column(String(128), nullable=False)
    capabilities = J(nullable=False, default=dict)  # {"vision":bool,"tools":bool,"json_mode":bool}
    is_default = Column(Boolean, nullable=False, default=False)
    fallback_rank = Column(Integer, nullable=False, default=0)  # 0=主用，越大越备用
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    user_email = Column(String(255), nullable=False, index=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="SET NULL"))
    scenario = Column(String(16), nullable=False)
    model = Column(String(128), nullable=False)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    user_email = Column(String(255), nullable=False, index=True)
    title = Column(String(255))
    kb_ids = J(nullable=False, default=list)  # 授权范围内可问的库
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user / assistant / system
    content = J(nullable=False)  # [{type:text},{type:image_url,...}] 结构化 parts
    citations = J()  # [{n,doc_name,chunk_id}] 引用溯源
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class License(Base):
    """授权记录表（§3.3 预留）。

    一期授权信任根是**厂商签名的授权文件**（Ed25519，见 services/license.py）：
    客户改库改不了签名，所以有效性判定不依赖本表。本表留给二期 SaaS——那时由服务端
    为各租户签发并在此落账，届时才是真正的授权台账。
    """
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    license_key = Column(String(128), nullable=False, unique=True)
    machine_fingerprint = Column(String(255))
    issued_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    features = J(nullable=False, default=dict)  # {"max_docs":5000,...}
    status = Column(String(16), nullable=False, default="active")


class UserSession(Base):
    """DB 会话表：cookie 存随机原文，库里只落 SHA-256——改密/禁用/登出即删行吊销。"""
    __tablename__ = "user_sessions"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class AuditLog(Base):
    """审计（§5 事件表）：只记认证+写操作；detail 永不落口令/密钥明文。"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    user_email = Column(String(255), index=True)  # 登录失败时可空（还没主体）
    action = Column(String(32), nullable=False, index=True)
    target_type = Column(String(32))
    target_id = Column(Integer)
    detail = J(nullable=False, default=dict)
    ip = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class UserKbGrant(Base):
    """库级授权（admin 隐式全库，不落行）。"""
    __tablename__ = "user_kb_grants"
    __table_args__ = (UniqueConstraint("user_id", "kb_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)


class AppSetting(Base):
    """运行时可改配置（§2.2 白标起步；"一切差异进配置"铁律的 DB 侧载体）。
    key 唯一，value JSONB；读多写少，端点侧自带默认值兜底。"""
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = J(nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
    updated_by = Column(String(255))


class ApiKey(Base):
    """开放 API 密钥（§2.2）：客户拿 Bearer key 调 OpenAI 兼容端点，把知识库嵌进自己的系统。
    key 明文只在创建响应里出现一次，库中只落 SHA-256；作用域（kb_ids）在检索层钳制，
    配额按自然月 token 计（从 usage_records 聚合）。"""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    name = Column(String(128), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True)
    key_prefix = Column(String(24), nullable=False, default="", server_default="")  # 打码展示
    kb_ids = J()  # NULL=全库；数组=限定库（语义同 user_kb_grants 的钳制方向）
    monthly_token_quota = Column(Integer)  # NULL=不限
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    last_used_at = Column(DateTime(timezone=True))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EvalQuestion(Base):
    """金标准问答集（§阶段2「评测体系正式化」）：评测的标尺本身，所以它自己也受审计。

    字段与 archive/stage0/eval/golden_qa.json 同源（expect_all/expect_any/cites/category/note），
    这样 stage0 的 20 题评测与产品内的评测是同一把尺子，历史报告能直接对照。
    首次启动按该 JSON 幂等播种（见 services/evaluation.load_golden）——客户开箱就有题可跑，
    不合适就在界面里删改（真题永远比预置题更懂客户的业务）。
    """
    __tablename__ = "eval_questions"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    question = Column(Text, nullable=False)
    expect_all = J(nullable=False, default=list)   # 必须全部出现的词
    expect_any = J(nullable=False, default=list)   # 至少出现其一（版本冲突题靠它抓"指出冲突"）
    cites = J(nullable=False, default=list)        # 期望命中的文档名——检索指标的金标准
    category = Column(String(64), nullable=False, default="", server_default="")
    note = Column(Text)                            # 校准记录：这题为什么这么判（防"为分数放水"）
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base):
    """一次评测。metrics 落库快照而不现算：历史运行的分数是"当时的证据"，
    不该随后来改配置/换模型而变——否则"上次 18/20、这次 20/20"根本无从比较。"""
    __tablename__ = "eval_runs"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    status = Column(String(16), nullable=False, default="running",
                    server_default="running", index=True)   # running/done/failed
    total = Column(Integer, nullable=False, default=0)      # 逐题推进：前端据此显示进度
    passed = Column(Integer, nullable=False, default=0)
    metrics = J(nullable=False, default=dict)
    kb_ids = J()                       # NULL=全部知识库；数组=限定库
    judge = Column(Boolean, nullable=False, default=False, server_default="false")
    # 本轮是否启用裁判模型（§阶段2 Ragas 侧）：区分"没启用"与"启用了但全判失败"——
    # 后者 judged>0 而 scored=0，前者两者皆 0，混在一起就说不清了
    chat_model = Column(String(128))
    embedding_model = Column(String(128))
    error = Column(Text)
    created_by = Column(String(255))
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True))


class EvalItemResult(Base):
    """单题结果。题目内容/判据全部**快照存档**：金标准题后来被改被删，
    历史运行的明细仍要能原样回看（含当时的期望词与备注）——否则回看历史等于拿新尺子量旧答案。"""
    __tablename__ = "eval_item_results"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("eval_runs.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("eval_questions.id", ondelete="SET NULL"))
    question = Column(Text, nullable=False)
    category = Column(String(64), nullable=False, default="", server_default="")
    note = Column(Text)
    expect_all = J(nullable=False, default=list)
    expect_any = J(nullable=False, default=list)
    cites = J(nullable=False, default=list)
    answer = Column(Text)
    cited_docs = J(nullable=False, default=list)
    top_docs = J(nullable=False, default=list)
    checks = J(nullable=False, default=dict)
    judge = J()   # LLM 裁判判词 {faithful, relevance, reason, raw, model}（NULL=本轮未启用裁判）
    passed = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
