# 建表前置条件：pgvector 扩展必须先于 create_all 存在。
# 真机复现（2026-10-08）：本地 Postgres.app 起全新库，build_production_app 的 create_all
# 直接在 chunks.embedding VECTOR(1024) 上炸 `type "vector" does not exist`——
# pgvector/pgvector 镜像只是"扩展可用"而不是"已安装"，全新数据卷的一键部署同样会踩。
# 本文件用"没有扩展的全新库"把这个失败复现一次并钉住修复。
import psycopg
import pytest
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.main import ensure_vector_extension

DB = "umaxrag_exttest"


@pytest.fixture
def fresh_db():
    s = get_settings()
    with psycopg.connect(s.psycopg_url("postgres"), autocommit=True) as con:
        con.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        con.execute(f'CREATE DATABASE "{DB}"')   # 有意不建 vector 扩展：这正是复现前提
    eng = create_engine(s.sqlalchemy_url(DB))
    yield eng
    eng.dispose()


def _has_extension(engine) -> bool:
    with engine.begin() as con:
        return con.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                           ).scalar() == 1


def test_ensure_vector_extension_makes_varchar_columns_creatable(fresh_db):
    assert not _has_extension(fresh_db), "前置条件不成立：新库不该已有 vector 扩展"
    ensure_vector_extension(fresh_db)
    assert _has_extension(fresh_db)
    # 关键断言：修完之后 VECTOR 列真的建得出来（原失败点就是这一步）
    with fresh_db.begin() as con:
        con.execute(text("CREATE TABLE probe (v vector(3))"))
    ensure_vector_extension(fresh_db)      # 幂等：重复调用不炸（每次启动都会走一遍）
