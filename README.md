# Umax_LangChainRagProject

> 把公司散落的文档，变成一个"随问随答、句句有出处"的企业智囊；一套系统做成模板，卖给一家复制一家。

面向中小企业（10~500 人）的企业级 RAG 知识库。经营模式：个人 OPC + vibecoding 开发，主打**私有化交付**（数据不出客户门）+ 授权费/年维保，后期扩多租户 SaaS。

## 技术栈

- 编排：LangChain（一期）+ LangGraph（三期 Agentic RAG）
- 后端：Python + FastAPI + PostgreSQL(pgvector) + ARQ 异步任务
- 解析：MinerU（扫描件/表格/图表）
- 检索：BM25 + 向量混合检索 + RRF 融合 + bge-reranker 重排
- 前端：Next.js + shadcn/ui（聊天界面 + 管理后台）
- 部署：Docker Compose 一键私有化交付

详细规划见 [产品需求与开发方案.md](./产品需求与开发方案.md)。

## 当前进度

- 🚧 **阶段 1 进行中（TDD）**：`backend/` 已完成 数据模型 9 表（§3.3）、检索内核、百炼 provider、**FastAPI 服务层**（知识库/上传入库/分块预览/检索/带引用问答+未命中兜底/会话历史/用量落账）——`cd backend && pytest` 48/48 绿，`pytest -m smoke` 真机全链路通过
- ✅ **阶段 0 完成（2026-09-19）**：金标准 20 题，纯 BM25 基线 20/20，混合检索 20/20（百炼 API 与本地 bge 双 provider 各验一轮）；执行记录与交接说明见[产品需求与开发方案.md](./产品需求与开发方案.md)第六章
- 链路：切块 → pgvector → BM25+向量 RRF → 重排 → qwen3.7-flash 带引用生成；嵌入/重排支持百炼 API 与本地双 provider，可 `.env` 切换
- 用法：`docker compose up -d` → `python ingest.py` → `python query.py "问题"` → `python eval.py` → `python compare_retrieval.py`
- 当前配置：百炼已放行全部模型，`EMBED_PROVIDER=bailian`（qwen3.7-text-embedding 1024 维 + qwen3.7-text-rerank），已重新入库；chat 间歇性 403 已内置重试
- 下一步：阶段 1 MVP（MinerU 解析 → FastAPI 服务化 → 模型网关 → 前端）
