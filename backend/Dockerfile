# 后端镜像：Umax RAG FastAPI 服务 + ARQ worker 共用（见 ../docker-compose.yml）
# 构建上下文=backend/：docker build -t umax-backend ./backend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
# 迁移必须进镜像：启动时 run_migrations 要读 alembic.ini + migrations/versions/——
# 少拷一个就是"容器起不来"，而本机没 Docker、构建期发现不了（靠 tests/test_migrations.py 的守卫钉住）
COPY alembic.ini ./
COPY migrations ./migrations

EXPOSE 8000
# 服务入口（uvicorn 绑定地址经 UVICORN_HOST 配，compose 置 0.0.0.0）；
# worker 入口：python -m arq app.worker.WorkerSettings（同一镜像换命令）
CMD ["python", "-m", "app.main"]
