from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().sqlalchemy_url(), pool_pre_ping=True)
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：请求级 Session。"""
    with Session(get_engine()) as session:
        yield session


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)
