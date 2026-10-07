# 一键交付手册（半天交付是硬指标，照此执行）

> 目标：新客户 = clone 仓库 + 填一份 `.env` + 跑一条 `docker compose up -d`，然后走初始化向导。

## 前置条件

- 客户机器：Linux（x86_64/arm64）或 macOS，4 核 8G 起步，Docker + Docker Compose v2 已安装
- 磁盘 ≥ 20G（镜像 + 文档 + 向量数据）

## 第 1 步：取代码与配置

```bash
git clone <模板仓库地址> umax && cd umax
cp .env.example .env
```

编辑 `.env`，必填两项：

| 项 | 说明 |
|----|------|
| `ADMIN_PASSWORD` | 初始管理员口令（至少 8 位强口令） |
| `GATEWAY_SECRET` | `openssl rand -hex 32` 生成，模型 key 加密主密钥 |

模型 key 推荐部署后在后台登记（客户 BYO-key，密钥加密存储、界面打码、不回传明文）。

## 第 2 步：一条命令起全套

```bash
docker compose up -d --build
```

- 异步入库档（文档量大时）：`.env` 里 `QUEUE_BACKEND=arq`，然后
  `docker compose --profile arq up -d --build`（多起 redis + worker）
- 国内镜像拉取不畅：见 `docker-compose.yml` 头部注释（镜像源 + 重新打 tag）

验证：`docker compose ps` 三个容器 healthy；浏览器打开 `http://<服务器IP>:3000`。

## 第 3 步：初始化向导（首登强改密）

1. 打开 `http://<服务器IP>:3000` → 登录页，用 `.env` 里的 `ADMIN_EMAIL/ADMIN_PASSWORD` 登录
2. **首登强制改密**：系统弹「首次登录，请修改初始口令」，改完才能进任何页面（后端 428 门闸兜底，绕过界面也进不去）
3. `/admin/models` 登记模型：chat / embedding / rerank（vision 可选）各一条，填厂商、地址、key、模型名
4. `/admin/kb` 建知识库 → 上传第一批文档 → 状态轮询到「就绪」
5. `/admin/apikeys` 签发 API key（可选）：给客户的钉钉/企微/内部系统用——OpenAI SDK 把 `base_url` 设为
   `http://<服务器IP>:8000/api/v1/openai`、key 填签发明文即可调 `/chat/completions`（回答自带 citations）
6. 聊天页提问验证「带引用回答」；`/admin/usage` 看用量；`/admin/users` 建成员账号并授权知识库（成员首登同样强制改密）

## 日常运维

| 操作 | 命令 |
|------|------|
| 看状态 | `docker compose ps` |
| 看日志 | `docker compose logs -f backend` |
| 升级版本 | `git pull && docker compose up -d --build` |
| 备份 | 停写后备份 `data/pg/`（全库含向量）与 `data/uploads/`（原始文件） |
| 数据不清盘重建容器 | 数据都在 `data/` 目录挂载里，`docker compose down` 不删数据 |

## 健康自检（减少售后的第一道闸）

- 后端：`curl http://<IP>:8000/api/v1/health` → `{"status":"ok"}`
- 前端：`curl -I http://<IP>:3000` → 200
- 组件异常先看 `docker compose ps` 哪个容器 unhealthy，再 `docker compose logs <服务名>`

## 已知边界（一期）

- HTTPS 由客户侧反代终结（nginx/caddy 对 3000 端口），编排内不自带证书
- MinerU 扫描件解析为可选外挂服务（`MINERU_BASE_URL`），未启用时上传扫描件会明确报错提示
- License 校验、白标设置按计划在后续阶段接入
