# Alembic 环境：target_metadata 取 app.models 的 Base，连接串取应用配置。
#
# 两条刻意的选择：
# ①URL 来自 app.core.config（.env + 环境变量），不落 alembic.ini——单一凭据来源；
# ②sys.path 按本文件位置补，而不是靠 cwd——从仓库根、backend/、CI 里跑都要能找到 app。
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import app.models  # noqa: E402,F401  注册全部表到 Base.metadata
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    """连接串：优先用程序化设置的（测试/CI 指向临时库），否则读应用配置。"""
    override = config.get_main_option("sqlalchemy.url", None)
    return override or get_settings().sqlalchemy_url()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
