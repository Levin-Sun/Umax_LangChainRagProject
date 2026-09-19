# 阶段 1 后端共享配置（pydantic-settings，读 backend/.env，环境变量优先）
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- PostgreSQL (pgvector) ----
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "rag"
    pg_password: str = "rag_dev_2026"
    pg_db: str = "umaxrag"

    # ---- 百炼（OpenAI 兼容协议）----
    dashscope_api_key: str = ""
    dashscope_compat_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_native_base: str = "https://dashscope.aliyuncs.com/api/v1"
    chat_model: str = "qwen3.7-flash"
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_dim: int = 1024
    rerank_model: str = "qwen3.7-text-rerank"

    def sqlalchemy_url(self, database: str | None = None) -> str:
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{database or self.pg_db}"
        )

    def psycopg_url(self, database: str) -> str:
        return self.sqlalchemy_url(database).replace("postgresql+psycopg://", "postgresql://")


TEST_DB = "umaxrag_test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
