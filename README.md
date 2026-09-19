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

- ✅ **阶段 0 基线已达标**：金标准 20 题 **20/20（100%）**，报告见 `stage0/eval/report_20260919_1819.md`
- 链路：切块 → pgvector 存储 → BM25 + 向量 RRF 混合检索 → gte-rerank 重排 → qwen3.7-flash 带引用生成
- 用法：`docker compose up -d` → `python ingest.py` → `python query.py "问题"` → `python eval.py`
- 说明：当前百炼专属端点仅放行 `qwen3.7-flash`（chat），向量与重排暂不可用，本次为**纯 BM25 基线**；接入 embedding/rerank 后需重跑对比，验证混合检索的增益
- 评测集校准记录：前两轮 95%/90% 的失败项均为"回答未包含题目未问及的延伸细节"，属评测标准过严，已校准（延伸细节降级为加分项，见 golden_qa.json 备注）
