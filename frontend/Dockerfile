# 前端镜像：Next.js standalone 产物（构建上下文=仓库根，需 sdk-ts 源码参与构建）
# 构建命令在 compose 里声明：
#   docker build -t umax-frontend -f frontend/Dockerfile .
FROM node:22-alpine AS build
WORKDIR /repo
# sdk-ts 源码先于依赖安装就位（@umax/sdk-ts 是 file:../sdk-ts 本地依赖）
COPY sdk-ts sdk-ts
COPY frontend frontend
RUN cd sdk-ts && npm ci --silent \
    && cd ../frontend && npm ci --silent && npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    # rewrite 的后端源（compose 网络内的 backend 服务）
    BACKEND_ORIGIN=http://backend:8000
# 构建上下文是仓库根，standalone 产物在 .next/standalone/frontend/ 下（带 frontend/ 一层）
COPY --from=build /repo/frontend/.next/standalone/frontend ./
COPY --from=build /repo/frontend/.next/static ./.next/static
COPY --from=build /repo/frontend/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
