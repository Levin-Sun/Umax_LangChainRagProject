# RBAC 新表落库往返与约束（真 PG；engine/db 夹具自动覆盖 sorted_tables）
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditLog, KnowledgeBase, User, UserKbGrant, UserSession
from app.services.auth import hash_password  # 任务 1 产物；本测试只借它造行，不测它


def _mk_user(db: Session, email="u@x.com", role="member"):
    u = User(tenant_id="default", email=email, name=email.split("@")[0],
             hashed_password=hash_password("x"), role=role)
    db.add(u)
    db.commit()
    return u


def test_user_new_columns_defaults(db: Session):
    u = _mk_user(db)
    got = db.get(User, u.id)
    assert got.name == "u" and got.status == "active" and got.role == "member"


def test_user_session_and_grant_roundtrip(db: Session):
    u = _mk_user(db)
    kb = KnowledgeBase(tenant_id="default", name="kbA")
    db.add(kb)
    db.commit()
    db.add_all([UserKbGrant(user_id=u.id, kb_id=kb.id),
                UserSession(token_hash="a" * 64, user_id=u.id,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=7))])
    db.commit()
    assert db.get(UserSession, "a" * 64).user_id == u.id
    assert [g.kb_id for g in db.query(UserKbGrant).filter_by(user_id=u.id)] == [kb.id]


def test_grant_unique_and_cascade(db: Session):
    u = _mk_user(db)
    kb = KnowledgeBase(tenant_id="default", name="kbB")
    db.add(kb)
    db.commit()
    db.add_all([UserKbGrant(user_id=u.id, kb_id=kb.id), UserKbGrant(user_id=u.id, kb_id=kb.id)])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_user_delete_cascades_sessions_and_grants(db: Session):
    """任务 2 欠账（评审收编⑥）：端点层没有删用户路径，吊销链只删会话——
    这里锁住真外键行为：账号一旦删除，其存活会话与库级授权必须随 CASCADE 消失，
    否则残留 user_id 会让 get_user 解出"无主会话"或留下越权授权。"""
    u = _mk_user(db)
    kb = KnowledgeBase(tenant_id="default", name="kbC")
    db.add(kb)
    db.commit()
    uid = u.id
    db.add_all([UserKbGrant(user_id=uid, kb_id=kb.id),
                UserSession(token_hash="c" * 64, user_id=uid,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=7))])
    db.commit()
    db.delete(db.get(User, uid))
    db.commit()
    assert db.query(UserSession).filter_by(user_id=uid).all() == []
    assert db.query(UserKbGrant).filter_by(user_id=uid).all() == []


def test_audit_log_row(db: Session):
    a = AuditLog(tenant_id="default", user_email="a@x.com", action="login_failed",
                 detail={"why": "bad"}, ip="127.0.0.1")
    db.add(a)
    db.commit()
    got = db.get(AuditLog, a.id)
    assert got.created_at is not None and got.user_email == "a@x.com"


def test_settings_admin_bootstrap_fields():
    from app.core.config import get_settings
    s = get_settings()
    assert hasattr(s, "admin_email") and hasattr(s, "admin_password")
