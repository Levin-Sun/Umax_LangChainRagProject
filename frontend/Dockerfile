# 前端镜像：Next.js standalone 产物（构建上下文=仓库根，需 sdk-ts 源码参与构建）
# 构建命令在 compose 里声明：
#   docker build -t umax-frontend -f frontend/Dockerfile .
FROM node:22-alpine AS build
WORKDIR /repo
# sdk-ts 源码先于依赖安装就位（@umax/sdk-ts 是 file:../sdk-ts 本地依赖）
COPY sdk-ts sdk-ts
COPY frontend frontend
# ⚠️ next.config 的 rewrites() 是 **构建期** 求值一次，结果烧进 .next/routes-manifest.json；
# standalone 的 server.js 启动后只读这份清单，运行期再设 BACKEND_ORIGIN 一律不生效。
# 所以后端源必须在这一层就给到（compose 通过 build.args 传，默认打 compose 网络内的 backend 服务）。
# 真机踩中：只在运行阶段设 → 产物里留的是开发缺省 127.0.0.1:8000 → 前端容器把 /api/v1/* 代理给自己，
# 登录页每次拉 /auth/me、/branding 都 ECONNREFUSED，浏览器看到 Internal Server Error。
ARG BACKEND_ORIGIN=http://backend:8000
ENV BACKEND_ORIGIN=${BACKEND_ORIGIN}
RUN cd sdk-ts && npm ci --silent \
    && cd ../frontend && npm ci --silent && npm run build

FROM node:22-alpine
WORKDIR /app
# 与构建层同源（同一个 ARG），保证 routes-manifest 里的目标与这里的值不会漂移
ARG BACKEND_ORIGIN=http://backend:8000
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    BACKEND_ORIGIN=${BACKEND_ORIGIN}
# 构建上下文是仓库根，standalone 产物在 .next/standalone/frontend/ 下（带 frontend/ 一层）
COPY --from=build /repo/frontend/.next/standalone/frontend ./
COPY --from=build /repo/frontend/.next/static ./.next/static
COPY --from=build /repo/frontend/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
