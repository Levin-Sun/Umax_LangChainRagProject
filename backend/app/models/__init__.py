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
    role = Column(String(16), nullable=False, default="member")  # admin / member
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
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True)
    tenant_id = T()
    license_key = Column(String(128), nullable=False, unique=True)
    machine_fingerprint = Column(String(255))
    issued_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    features = J(nullable=False, default=dict)  # {"max_docs":5000,...}
    status = Column(String(16), nullable=False, default="active")
