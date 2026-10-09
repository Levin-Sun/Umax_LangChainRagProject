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

# 版本印记（/health 的 commit）：镜像内没有 .git，version.py 的 `git rev-parse` 那条路走不通，
# 不打这个参数就永远是 unknown——而 DEPLOY.md 恰恰把 commit 当「改了不生效」的第一手依据，
# 于是最需要它的时候它正好是空的（真机 2026-10-09 实测）。放在 pip 层之后：改版本号不该废掉依赖缓存。
# 构建时给值：BUILD_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}

EXPOSE 8000
# 服务入口（uvicorn 绑定地址经 UVICORN_HOST 配，compose 置 0.0.0.0）；
# worker 入口：python -m arq app.worker.WorkerSettings（同一镜像换命令）
CMD ["python", "-m", "app.main"]
