# TDD 红灯：数据模型（需求文档 §3.3）——先于实现提交
# 约定：所有业务表带 tenant_id（一期恒为 'default'，二期 SaaS 启用）；
# messages.content 为结构化 content parts（天生多模态）；chunks 存 pgvector 向量。
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _tables():
    from app.db.base import Base
    return set(Base.metadata.tables)


def test_all_tables_defined():
    assert {
        "users", "knowledge_bases", "documents", "chunks",
        "model_configs", "usage_records", "conversations", "messages", "licenses",
    } <= _tables(), f"缺表: { {'users','knowledge_bases','documents','chunks','model_configs','usage_records','conversations','messages','licenses'} - _tables() }"


def test_user_and_kb_roundtrip(db: Session):
    from app.models import KnowledgeBase, User

    u = User(tenant_id="default", email="a@b.com", hashed_password="x", role="admin")
    kb = KnowledgeBase(tenant_id="default", name="产品手册库", description="含SKU规范")
    db.add_all([u, kb])
    db.commit()
    got = db.query(User).one()
    assert (got.email, got.role, got.tenant_id) == ("a@b.com", "admin", "default")
    assert db.query(KnowledgeBase).one().name == "产品手册库"


def test_email_unique_per_tenant(db: Session):
    from app.models import User

    db.add(User(tenant_id="t1", email="dup@x.com", hashed_password="x"))
    db.commit()
    db.add(User(tenant_id="t1", email="dup@x.com", hashed_password="y"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # 不同租户同邮箱必须允许（二期多租户）
    db.add(User(tenant_id="t2", email="dup@x.com", hashed_password="x"))
    db.commit()
    assert db.query(User).count() == 2


def test_document_status_machine_and_cascade(db: Session):
    from app.models import Chunk, Document, KnowledgeBase

    kb = KnowledgeBase(tenant_id="default", name="kb")
    db.add(kb)
    db.flush()
    doc = Document(tenant_id="default", kb_id=kb.id, name="ops.pdf", status="pending")
    db.add(doc)
    db.flush()
    db.add_all([
        Chunk(tenant_id="default", document_id=doc.id, kb_id=kb.id,
              chunk_index=i, content=f"c{i}")
        for i in range(3)
    ])
    db.commit()

    doc.status = "ready"
    db.commit()
    assert db.query(Document).one().status == "ready"

    db.delete(doc)
    db.commit()
    assert db.query(Chunk).count() == 0, "删文档必须级联删切块"


def test_chunk_vector_cosine_query(db: Session):
    """真 pgvector：向量列可写可读，<=> 余弦距离排序正确。"""
    from sqlalchemy import text as sa_text

    from app.core.config import get_settings
    from app.models import Chunk, Document, KnowledgeBase

    dim = get_settings().embedding_dim
    near_vec = [1.0] + [0.0] * (dim - 1)
    far_vec = [0.0, 1.0] + [0.0] * (dim - 2)
    query_vec = [0.9, 0.1] + [0.0] * (dim - 2)

    kb = KnowledgeBase(tenant_id="default", name="kb")
    db.add(kb)
    db.flush()
    doc = Document(tenant_id="default", kb_id=kb.id, name="d.txt", status="ready")
    db.add(doc)
    db.flush()
    db.add_all([
        Chunk(tenant_id="default", document_id=doc.id, kb_id=kb.id, chunk_index=0,
              content="near", embedding=near_vec),
        Chunk(tenant_id="default", document_id=doc.id, kb_id=kb.id, chunk_index=1,
              content="far", embedding=far_vec),
    ])
    db.commit()

    rows = db.execute(
        sa_text("SELECT content, embedding <=> CAST(:q AS vector) AS dist "
                "FROM chunks ORDER BY dist LIMIT 2"),
        {"q": "[" + ",".join(f"{x:.4f}" for x in query_vec) + "]"},
    ).all()
    assert [r[0] for r in rows] == ["near", "far"]
    assert rows[0][1] < rows[1][1]


def test_model_config_capabilities_and_flags(db: Session):
    from app.models import ModelConfig

    mc = ModelConfig(
        tenant_id="default", scenario="chat", provider="dashscope",
        base_url="https://x/compatible-mode/v1", encrypted_api_key="gcm:blob",
        model_name="qwen3.7-flash",
        capabilities={"vision": False, "tools": True, "json_mode": True},
        is_default=True, fallback_rank=0, enabled=True,
    )
    db.add(mc)
    db.commit()
    got = db.query(ModelConfig).one()
    assert got.capabilities["tools"] is True
    assert got.scenario == "chat"


def test_message_content_parts_multimodal(db: Session):
    """§2.1-B：消息从第一天用结构化 content parts，不存纯字符串。"""
    from app.models import Conversation, Message

    conv = Conversation(tenant_id="default", user_email="a@b.com", title="退货政策")
    db.add(conv)
    db.flush()
    db.add(Message(
        tenant_id="default", conversation_id=conv.id, role="user",
        content=[{"type": "text", "text": "这个商品能退吗"},
                 {"type": "image_url", "image_url": {"url": "s3://bucket/x.png"}}],
        citations=[{"n": 1, "doc_name": "manual.pdf", "chunk_id": 42}],
    ))
    db.commit()
    m = db.query(Message).one()
    assert m.content[1]["type"] == "image_url"
    assert m.citations[0]["doc_name"] == "manual.pdf"


def test_usage_record_aggregation(db: Session):
    from sqlalchemy import func

    from app.models import UsageRecord

    now = datetime.now(timezone.utc)
    db.add_all([
        UsageRecord(tenant_id="default", user_email="a@b.com", scenario="chat",
                    model="qwen3.7-flash", prompt_tokens=100, completion_tokens=20,
                    latency_ms=900, created_at=now),
        UsageRecord(tenant_id="default", user_email="a@b.com", scenario="chat",
                    model="qwen3.7-flash", prompt_tokens=50, completion_tokens=10,
                    latency_ms=400, created_at=now),
    ])
    db.commit()
    total = db.query(
        func.sum(UsageRecord.prompt_tokens), func.sum(UsageRecord.completion_tokens)
    ).one()
    assert tuple(total) == (150, 30)


def test_license_expiry(db: Session):
    from app.models import License

    lic = License(
        tenant_id="default", license_key="UMAX-DEMO",
        machine_fingerprint="sha256:abc",
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
        features={"max_docs": 5000},
    )
    db.add(lic)
    db.commit()
    got = db.query(License).one()
    assert got.expires_at > datetime.now(timezone.utc)
    assert got.features["max_docs"] == 5000
