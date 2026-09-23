# 测试基建：真 PG 容器，独立测试库 umaxrag_test（决策见交接记忆：集成测试不 mock 数据库）
import psycopg
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import TEST_DB, get_settings
from app.db.base import Base
import app.models  # noqa: F401  注册全部表到 Base.metadata

settings = get_settings()


def _admin_url() -> str:
    return settings.psycopg_url("postgres")


@pytest.fixture(scope="session")
def engine() -> Engine:
    """会话级：重建测试库（drop 重建=最快迁移），建 vector 扩展，create_all。"""
    with psycopg.connect(_admin_url(), autocommit=True) as con:
        con.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        con.execute(f'CREATE DATABASE "{TEST_DB}"')
    eng = create_engine(settings.sqlalchemy_url(TEST_DB))
    with eng.begin() as con:
        con.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Session:
    """函数级：清空全部业务表后提供 Session，保证测试互不污染。"""
    tables = ", ".join(
        f'"{t.name}"' for t in Base.metadata.sorted_tables
    )
    if tables:
        with engine.begin() as con:
            con.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with Session(engine) as session:
        yield session


# ---- RBAC 测试助手（任务 3 起共享）----
def seed_user(engine, email: str, password: str, *, role: str = "member", name: str = "") -> int:
    """直写 users 表（绕过端点，端点行为另有测试）。返回 user id。"""
    from app.models import User
    from app.services.auth import hash_password
    with Session(engine) as s:
        u = User(tenant_id="default", email=email, name=name or email.split("@")[0],
                 hashed_password=hash_password(password), role=role, status="active")
        s.add(u)
        s.commit()
        return u.id


def login(client, email: str, password: str) -> None:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 204, f"登录失败：{r.status_code} {r.text}"
