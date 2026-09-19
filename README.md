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

- ✅ **阶段 0 完成（2026-09-19）**：金标准 20 题，纯 BM25 基线 20/20，混合检索（本地 bge-small-zh-v1.5 向量 + bge-reranker-base 重排）20/20；执行记录与交接说明见[产品需求与开发方案.md](./产品需求与开发方案.md)第六章
- 链路：切块 → pgvector → BM25+向量 RRF → 重排 → qwen3.7-flash 带引用生成；嵌入/重排支持百炼 API 与本地双 provider，可 `.env` 切换
- 用法：`docker compose up -d` → `python ingest.py` → `python query.py "问题"` → `python eval.py` → `python compare_retrieval.py`
- 已知：百炼专属端点暂仅放行 qwen3.7-flash（embedding/rerank 403，控制台开通未生效），向量走本地模型；chat 有间歇性 403 已内置重试
- 下一步：阶段 1 MVP（MinerU 解析 → FastAPI 服务化 → 模型网关 → 前端）
