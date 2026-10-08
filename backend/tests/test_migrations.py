# Alembic 接管建表/升级的行为锁定（评审遗留：手写幂等 ALTER → 真迁移）。
#
# 验收标准刻意用 **Alembic 自己的比较器**（compare_metadata）而不是"表在不在"：
# 迁移跑完的结果与 app/models 的差异必须是 **0 条**。这样"迁移脚本和模型漂移"
# （改了模型忘了加 revision / 改了 revision 忘了改模型）就从"要人记得核对"变成会红的测试——
# 这正是引入 Alembic 想要的东西，否则迁移脚本本身也会变成第二份会腐烂的事实源。
import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401  注册全部表到 Base.metadata
from app.core.config import get_settings
from app.db.base import Base
from app.main import run_migrations

FRESH_DB = "umaxrag_migfresh"
LEGACY_DB = "umaxrag_miglegacy"


def _recreate(name: str):
    s = get_settings()
    with psycopg.connect(s.psycopg_url("postgres"), autocommit=True) as con:
        con.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        con.execute(f'CREATE DATABASE "{name}"')
    return create_engine(s.sqlalchemy_url(name))


def _diff_vs_models(engine) -> list:
    """迁移后的库 vs 模型：Alembic 视角的差异（0 条=完全对齐）。"""
    with engine.connect() as con:
        ctx = MigrationContext.configure(con, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)


@pytest.fixture
def fresh_db():
    eng = _recreate(FRESH_DB)
    yield eng
    eng.dispose()


@pytest.fixture
def legacy_db():
    """模拟"引入 Alembic 之前"的库：create_all 建的全部表，但没有 alembic_version。"""
    eng = _recreate(LEGACY_DB)
    with eng.begin() as con:
        con.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def test_fresh_database_migrates_to_exact_model_schema(fresh_db):
    """全新库：一条 `upgrade head` 建齐全部表，且与模型零差异。"""
    run_migrations(fresh_db)
    tables = set(inspect(fresh_db).get_table_names())
    assert "alembic_version" in tables
    assert _diff_vs_models(fresh_db) == []          # 迁移 == 模型（18 张业务表逐列逐索引）


def test_legacy_database_is_stamped_not_rebuilt(legacy_db):
    """既有库（create_all 时代）：盖章接管，**不重跑建表 DDL**（重跑必然"表已存在"失败）。

    这条是引入 Alembic 当口的兼容动作，也正是用户实例要走的那条路。
    """
    before = {t: len(inspect(legacy_db).get_columns(t))
              for t in inspect(legacy_db).get_table_names()}
    run_migrations(legacy_db)          # 不抛异常即证明没有重复 create_table
    after = {t: len(inspect(legacy_db).get_columns(t))
             for t in inspect(legacy_db).get_table_names()}
    assert "alembic_version" in after
    assert {k: v for k, v in after.items() if k != "alembic_version"} == before


def test_migrations_are_idempotent(fresh_db, legacy_db):
    """启动多少次都是同一结果（部署脚本/容器重启会反复调它）。"""
    for eng in (fresh_db, legacy_db):
        run_migrations(eng)
        sig = {t: len(inspect(eng).get_columns(t)) for t in inspect(eng).get_table_names()}
        run_migrations(eng)
        assert {t: len(inspect(eng).get_columns(t))
                for t in inspect(eng).get_table_names()} == sig
        assert _diff_vs_models(eng) == []


def test_vector_extension_created_before_migration(fresh_db):
    """VECTOR(1024) 列要求扩展先存在——全新库（连扩展都没有）也必须能一次跑通。"""
    assert inspect(fresh_db).get_table_names() == []
    run_migrations(fresh_db)
    with fresh_db.connect() as con:
        assert con.execute(text("SELECT extname FROM pg_extension "
                                "WHERE extname = 'vector'")).scalar() == "vector"


def test_backend_image_copies_migration_assets():
    """容器里也要能迁移：Dockerfile 必须把 alembic.ini 与 migrations/ 拷进镜像。

    本机没有 Docker，构建期这块是盲区——一旦少拷一个，容器启动直接死在 run_migrations，
    而所有单测照样全绿（它们跑在宿主机上）。所以用一条静态断言把盲区照亮。
    """
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert "COPY alembic.ini" in text
    assert "COPY migrations" in text
