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

- 阶段 0 技术验证进行中：`stage0/` 已搭建完整链路（切块 → text-embedding-v4 向量化 → pgvector 存储 → BM25+向量 RRF 混合检索 → gte-rerank 重排 → qwen-plus 带引用生成）
- 金标准评测集 20 题（`stage0/eval/golden_qa.json`），覆盖错别字、版本冲突、同义术语、私人噪声、中英混杂、作废内容识别等考察点
- 用法：`docker compose up -d` → `python ingest.py` → `python query.py "问题"` → `python eval.py`
- 当前阻塞：百炼 API key 被限制（Access denied by API-Key restrictions），待账户侧解除后跑基线评测
