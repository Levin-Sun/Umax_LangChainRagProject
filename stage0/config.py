# 阶段 0 技术验证——共享配置
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---- 阿里云百炼（DashScope）----
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
# OpenAI 兼容入口（chat / embeddings）
COMPAT_BASE = os.getenv(
    "DASHSCOPE_COMPAT_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
# 原生入口（rerank 等兼容模式没有的能力）
NATIVE_BASE = os.getenv("DASHSCOPE_NATIVE_BASE", "https://dashscope.aliyuncs.com/api/v1")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen-plus")
RERANK_MODEL = os.getenv("RERANK_MODEL", "gte-rerank-v2")

# ---- PostgreSQL (pgvector) ----
PG = {
    "host": os.getenv("PG_HOST", "localhost"),
    "port": int(os.getenv("PG_PORT", "5432")),
    "user": os.getenv("PG_USER", "rag"),
    "password": os.getenv("PG_PASSWORD", "rag_dev_2026"),
    "database": os.getenv("PG_DB", "umaxrag"),
}

# ---- 语料与切块 ----
DOCS_DIR = Path(__file__).parent.parent / "DirtyDocs"
CHUNK_TARGET = 300   # 切块目标长度（字符）
CHUNK_MIN = 60       # 小于该长度的段落并入上一块

# ---- 检索参数 ----
RECALL_K = 10        # 每路召回数
RRF_K = 60           # RRF 融合常数
RERANK_TOP_N = 5     # 重排后保留数
