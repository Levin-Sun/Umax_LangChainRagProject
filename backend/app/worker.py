# ARQ worker：慢活（解析→切块→向量化）排队执行，界面轮询 documents.status
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Document, KnowledgeBase
from app.services.ingest import ingest_document


def run_import(*, document_id: int, engine=None, embedder=None, mineru=None,
               vision_fn=None, caption_images: bool = True, settings_store=None) -> dict:
    """执行单个文档的导入流水线（ARQ worker 与同步模式共用）。

    切块参数**必须走配置中心**（SettingsStore.chunk_params）而不是 `.env`：
    建库锁定值优先、其次配置中心生效值——与同步端点同口径。曾经这里直接用
    `s.chunk_target`，导致两种部署模式切块结果不同（评审发现的真机问题）。
    """
    s = get_settings()
    own_session = engine is None
    if own_session:
        engine = create_engine(s.sqlalchemy_url())
    store = settings_store
    if store is None:                       # 未注入（如 CLI/一次性调用）：按引擎现建
        from app.services.settings import SettingsStore
        store = SettingsStore(engine, s)
    with Session(engine) as session:
        doc = session.get(Document, document_id)
        if doc is None:
            return {"document_id": document_id, "status": "failed",
                    "error": f"文档 {document_id} 不存在"}
        kb = session.get(KnowledgeBase, doc.kb_id)
        try:                                # 原文件读不出来要有失败态，不能让文档卡在 pending
            with open(doc.storage_path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            doc.status, doc.error = "failed", f"原文件不可读：{exc}"
            session.commit()
            return {"document_id": doc.id, "status": doc.status, "error": doc.error}
        doc = ingest_document(session, doc, raw, embedder=embedder, mineru=mineru,
                              vision_fn=vision_fn, caption_images=caption_images,
                              **store.chunk_params(kb))
        return {"document_id": doc.id, "status": doc.status, "error": doc.error}


# ---- ARQ 入口：python -m arq app.worker.WorkerSettings ----

async def _startup(ctx: dict) -> None:
    s = get_settings()
    from app.services.embeddings import BailianEmbedder
    from app.services.parsers import MinerUClient

    ctx["engine"] = create_engine(s.sqlalchemy_url(), pool_pre_ping=True)
    ctx["embedder"] = BailianEmbedder(api_key=s.dashscope_api_key,
                                      base_url=s.dashscope_compat_base,
                                      model=s.embedding_model,
                                      dimensions=s.embedding_dim)
    ctx["mineru"] = MinerUClient(s.mineru_base_url) if s.mineru_base_url else None
    # 视觉能力与配置中心同口径：ARQ 档也要给文档图注（否则异步入库静默丢图）
    from app.services.settings import SettingsStore

    store = SettingsStore(ctx["engine"], s)      # 配置中心：切块参数与图注开关共用同一实例
    ctx["settings_store"] = store
    ctx["vision_fn"] = None
    ctx["caption_images"] = bool(store.effective()["doc_image_caption"])
    if s.gateway_secret:
        from app.services.gateway import ModelGateway

        ctx["vision_fn"] = ModelGateway(ctx["engine"], secret=s.gateway_secret).make_vision_fn(
            vision_prompt=lambda: store.effective()["vision_prompt"])


async def import_document(ctx: dict, document_id: int) -> dict:
    return run_import(document_id=document_id, engine=ctx["engine"],
                      embedder=ctx.get("embedder"), mineru=ctx.get("mineru"),
                      vision_fn=ctx.get("vision_fn"),
                      caption_images=ctx.get("caption_images", True),
                      settings_store=ctx.get("settings_store"))


class WorkerSettings:
    from arq.connections import RedisSettings

    # Redis 地址走统一配置（REDIS_HOST/REDIS_PORT）：容器部署 worker 连 compose 网络的 redis 服务
    redis_settings = RedisSettings(host=get_settings().redis_host, port=get_settings().redis_port)
    functions = [import_document]
    on_startup = _startup
    max_jobs = 4  # 解析吃内存，单机起步
