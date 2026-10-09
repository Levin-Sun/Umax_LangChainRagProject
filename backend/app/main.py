# FastAPI 服务层：知识库/文档入库/检索/带引用问答/会话历史
# 鉴权收口（阶段 2·任务 6）：除 /health 与 /auth/login 外全部端点要求登录会话；
# admin 面（kb 写/文档写/models/usage/users/grants/audit）另加角色校验，member 得 403。
# 库级授权在检索层钳制（services/retrieval.allowed_kb_ids），不在展示层过滤。
# 旧 ADMIN_TOKEN 共享口令方案（require_admin/admin_session/admin_hint//admin/*）已整体退役，
# 不留兼容层；初始管理员由 build_production_app 按 ADMIN_EMAIL/ADMIN_PASSWORD 播种。
#
# 本文件在"评审遗留：拆 router"这轮之后只剩**装配**：create_app 把依赖收进 Runtime、
# 注册各域 router、挂中间件；每个域的实现搬到 app/api/routers/<域>.py。
# 端点体是逐字搬移的（唯一变化是 @app.→@router. 与闭包变量改走 rt.*），
# 所以这次重构的验收标准是 contracts/openapi.json **逐字节不变**——spec 是唯一事实源，
# 它没变就说明契约面没动。
from collections.abc import Callable
from pathlib import Path

import logging
import threading

from fastapi import FastAPI
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api.deps import Runtime, now
from app.api.middleware import _AllowHeaderMiddleware
from app.api.routers import BUILDERS
from app.api.schemas import build_settings_put_model
from app.core.config import get_settings
from app.models import EvalQuestion, UsageRecord, User
from app.services.auth import LoginThrottle, hash_password
from app.services.compose import (make_embedder, with_gateway_fallback, with_rerank_degrade,
                                  with_rerank_fallback)
from app.services.evaluation import load_golden
from app.services.jobs import recover_interrupted_jobs
from app.services.judge import make_judge_fn
from app.services.settings import SettingsStore


def ensure_vector_extension(engine: Engine) -> None:
    """建表前必须先有的东西：pgvector 扩展。

    真机踩过（2026-10-08，本地 Postgres.app 起全新库）：`chunks.embedding` 是 VECTOR(1024) 列，
    扩展不在就直接 `type "vector" does not exist` —— 一键部署会死在 create_all 这一步。
    pgvector/pgvector 镜像只是**扩展可用**，不是**已安装**，全新数据卷同样会踩。
    IF NOT EXISTS 幂等：客户 DBA 预装过就空转（PG 先判存在即跳过，不会因权限被拦）。
    """
    with engine.begin() as con:
        con.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _alembic_config(engine: Engine):
    """按需构造 Alembic 配置（不读 alembic.ini 里的 URL：连接串只有应用配置一个来源）。"""
    from alembic.config import Config

    cfg = Config(str(MIGRATIONS_DIR.parent / "alembic.ini"))
    # script_location 给绝对路径：从仓库根 / backend / CI 哪启动都找得到
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # 连接串直接用**手上这个 engine**：迁移与应用必须打在同一个库上，
    # 从环境变量再读一遍就会出现"迁移打到 A 库、应用连 B 库"这种没法排查的分叉。
    # hide_password=False 是必须的——密文留给 SQLAlchemy 自己用，它只活在这个内存对象里。
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    return cfg


def run_migrations(engine: Engine) -> None:
    """建表/升级的唯一入口（Alembic 接管，替代 create_all + 手写幂等 ALTER）。

    两种库、两种走法：
    - **全新库**（没有任何表）：`upgrade head`，一次把 18 张表建齐；
    - **既有库**（create_all 时代建的，没有 `alembic_version` 表）：它的结构已经等于基线
      （当时的幂等 ALTER 就是干这个的），所以先 `stamp head` 盖章接管，**不重跑 DDL**——
      重跑必然"表已存在"失败。这一步只在引入 Alembic 的当口有意义，此后不再出现。

    判定只看 `alembic_version` 在不在，所以幂等：启动多少次都是同一结果。
    此后所有结构变更都必须是**新增一条 revision**（见 migrations/versions/），
    不再往这里堆 ALTER。
    """
    from alembic import command
    from sqlalchemy import inspect

    ensure_vector_extension(engine)   # 必须先于迁移：chunks.embedding 是 VECTOR 列
    cfg = _alembic_config(engine)
    tables = set(inspect(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        logging.getLogger("umax").warning(
            "检测到 Alembic 接管前建的库（%d 张表、无 alembic_version）：按基线盖章接管，"
            "不重跑建表 DDL", len(tables))
        command.stamp(cfg, "head")
    command.upgrade(cfg, "head")


def create_app(
    *,
    engine: Engine,
    embedder=None,
    chat_fn: Callable[[str, list[dict]], dict] | None = None,
    vision_fn: Callable[[str], dict] | None = None,   # (image_data_url) -> {caption,...}；None=无视觉模型
    upload_dir: str = "uploads",
    queue=None,          # ImportQueue 协议；None=同步入库
    mineru=None,         # MinerUClient；None=扫描件解析直接失败并说明原因
    secret: str | None = None,  # 网关主密钥（GATEWAY_SECRET），None=读配置
    license_public_key: str | None = None,   # License 公钥；空=开发模式不校验
    license_file: str | None = None,         # 授权文件路径
    machine_fingerprint: str | None = None,  # 本机指纹（测试注入；None=现算）
    settings_store=None,     # 运行时配置中心；None=按 engine+.env 现建
    spawn: Callable[[Callable[[], None]], None] | None = None,
    # 后台任务启动器（评测用）。None=真线程；测试注入同步实现（lambda fn: fn()），
    # 让"后台跑完再看结果"在单测里是确定性的——不靠 sleep 赌时序
    judge_fn: Callable[[str, str, list[dict]], dict] | None = None,
    # LLM 裁判（§阶段2 Ragas 侧）：(question, answer, hits) -> 判词；None=未配置裁判通路（请求评测带 judge 即 400）
    rerank_fn: Callable[[str, list[dict]], list[dict]] | None = None,
    # 精排（§3.2）：(query, 候选) -> 重排后的候选；None=未配置 rerank 模型（检索就用 RRF 融合顺序）
) -> FastAPI:
    app = FastAPI(title="Umax RAG", version="0.1.0")
    s = get_settings()
    # 运行时配置中心（§D）：提示词/检索/切块参数后台可改，每请求现读＝改完即生效
    cfg = settings_store if settings_store is not None else SettingsStore(engine, s)

    def _spawn_default(fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    # 依赖全在一个 Runtime 上：多实例（测试/多进程）天然隔离——登录节流计数、
    # 精排降级外壳、license 文件路径这些都是"每个 app 一份"，从来不是进程级单例
    rt = Runtime(
        engine=engine,
        settings=s,
        cfg=cfg,
        gateway_secret=s.gateway_secret if secret is None else secret,
        spawn=spawn or _spawn_default,
        # 精排：装上"失败即降级"的外壳，四处检索调用共用同一份（口径只有一处）
        rerank=with_rerank_degrade(rerank_fn),
        embedder=embedder,
        chat_fn=chat_fn,
        vision_fn=vision_fn,
        judge_fn=judge_fn,
        upload_dir=upload_dir,
        queue=queue,
        mineru=mineru,
        throttle=LoginThrottle(),   # 每 app 实例独立计数：测试互污染为零
        license_public_key=s.license_public_key if license_public_key is None else license_public_key,
        license_file=Path(license_file) if license_file else s.license_file,
        license_fingerprint=machine_fingerprint or None,
        started_at=now().isoformat(),
        settings_put_model=build_settings_put_model(cfg.spec),
    )
    app.state.runtime = rt
    routers = [build_router(rt) for build_router in BUILDERS]
    for router in routers:
        app.include_router(router)

    # 刻意传**拍平后的路由表**而不是 app.router.routes：include_router 会把子路由包成
    # 一层 _IncludedRouter（不带 .methods），中间件按 .methods 找路径方法时会一个都找不到——
    # Allow 头静默失效（拆 router 时真踩到，靠契约 fuzz 的 AllowHeaderMismatch 才能发现）。
    app.add_middleware(_AllowHeaderMiddleware,
                       routes=[route for r in routers for route in r.routes])
    return app


def build_production_app(upload_dir: str = "uploads",
                         engine: Engine | None = None) -> FastAPI:
    """真依赖装配：配了 GATEWAY_SECRET 且表里有对应场景模型 → 走网关（后台改表即生效）；
    否则退回 .env 里的百炼直连（阶段 0 链路，冒烟可跑）。"""
    from sqlalchemy import create_engine

    from app.services.chat import ChatClient, make_chat_fn
    from app.services.parsers import MinerUClient
    from app.services.rerank import RerankClient, reorder

    s = get_settings()
    if engine is None:
        engine = create_engine(s.sqlalchemy_url(), pool_pre_ping=True)
    # 建表/升级唯一入口：Alembic（替代此前的 create_all + 累积的幂等 ALTER——
    # 那套东西每加一列就要多一条 ALTER，且"客户库现在到底缺哪列"没人能一眼回答）。
    # 注入 engine 的场景（测试）同样走它：既有测试库会被按基线盖章，幂等。
    run_migrations(engine)
    # 播种初始管理员（spec §2）：users 空表时按 ADMIN_EMAIL/ADMIN_PASSWORD 落一条 role=admin，
    # 口令 hash 后入库（明文永不落库）。测试装配 create_app 不播种，走 conftest.seed_user——
    # 故这里只在 build_production_app 里做，且放在 engine 判定之后（注入 engine 也要播种）。
    with Session(engine) as ses:   # ses 而非 s：s 已是 Settings，with 目标名会覆盖函数作用域
        if ses.query(User).first() is None:
            ses.add(User(tenant_id="default", email=s.admin_email, name="admin",
                         hashed_password=hash_password(s.admin_password), role="admin",
                         must_change_password=True))   # 初始口令是模板口令，首登强改密（初始化向导第一屏）
            ses.commit()
            logging.getLogger("umax").warning(
                "已播种初始管理员 %s（ADMIN_EMAIL/ADMIN_PASSWORD）——首次登录强制修改口令", s.admin_email)
        # 金标准集播种（§阶段2）：空表时灌入随应用打包的预置集（与 stage0 同源 20 题）。
        # "评测集第一天就建"这条风险对策要开箱即生效——让客户自己攒题，这件事一定被拖到永远。
        # 只判空表：客户删改过的题集不会被下次启动覆盖回去（预置集是起手牌，不是主人）。
        if ses.query(EvalQuestion).first() is None:
            gold = load_golden()
            if gold:
                ses.add_all([EvalQuestion(tenant_id="default", **row) for row in gold])
                ses.commit()
                logging.getLogger("umax").info(
                    "已播种 %d 条预置金标准题（可在评测页删改）", len(gold))
    chat_fn = embedder = vision_fn = None
    judge_fn = None
    bailian_chat = None
    cfg_store = SettingsStore(engine, s)          # 配置中心：端点与生成层共用同一实例
    prompt_of = lambda key: (lambda: cfg_store.effective()[key])  # noqa: E731  每次调用现取
    bailian_client = None
    bailian_rerank = None
    if s.dashscope_api_key:
        # .env 百炼直连（阶段 0 链路）：作为网关表未配置时的开发/冒烟兜底
        bailian_client = ChatClient(api_key=s.dashscope_api_key,
                                    base_url=s.dashscope_compat_base, model=s.chat_model)
        bailian_chat = make_chat_fn(bailian_client,
                                    system_prompt=prompt_of("chat_system_prompt"))
        if s.rerank_model:   # 重排只在原生端点（兼容端点没有 /rerank，实测返回空）
            direct_rerank = RerankClient(api_key=s.dashscope_api_key,
                                         base_url=s.dashscope_native_base,
                                         model=s.rerank_model)

            def bailian_rerank(query: str, hits: list[dict]) -> list[dict]:
                """与网关侧同形的适配器。**记账口径也一致**（scenario=rerank、归检索链路）：
                重排是花钱的一步，无论走网关还是 .env 直连都必须进台账，否则"成本闸门"漏一边。
                """
                out = direct_rerank.rerank(query, [h.get("content", "") for h in hits],
                                           top_n=len(hits))
                with Session(engine) as ses:
                    ses.add(UsageRecord(tenant_id="default", user_email="system@local",
                                        kb_id=(hits[0].get("kb_id") if hits else None),
                                        scenario="rerank", model=s.rerank_model,
                                        prompt_tokens=out["prompt_tokens"],
                                        completion_tokens=0))
                    ses.commit()
                return reorder(hits, out["results"])
    gw = None
    if s.gateway_secret:
        from app.services.gateway import ModelGateway

        gw = ModelGateway(engine, secret=s.gateway_secret)
        # 运行时换模型即生效（§C）：网关 fn 每次调用现读表，启动时表空不再"判死"——
        # 后台登记第一个模型立即接线；表空且无百炼 → chat MISS / 入库 BM25-only 降级
        chat_fn = with_gateway_fallback(gw.make_chat_fn(system_prompt=prompt_of("chat_system_prompt")),
                                        bailian_chat)
        embedder = make_embedder(s, gateway=gw)   # 与 ARQ worker 共用同一处装配（见 compose.make_embedder）
        vision_fn = gw.make_vision_fn(vision_prompt=prompt_of("vision_prompt"))  # 未配 vision 场景则端点降级
    else:
        chat_fn, embedder = bailian_chat, make_embedder(s)

    def _judge_complete(messages: list[dict]) -> dict:
        """裁判的通路：网关优先（后台换裁判模型即生效），表里没有 chat 模型时退 .env 直连。

        "配了但全挂"（GatewayError）**刻意不降级**——与问答同一口径：那是配置错误要修，
        静默换一家只会让"为什么分数变了"变成无解之谜。
        """
        from app.services.gateway import NoProviderError

        if gw is not None:
            try:
                return gw.chat(messages, log=False)
            except NoProviderError:
                if bailian_client is None:
                    raise
        if bailian_client is None:
            raise NoProviderError("没有可用的裁判通路（网关未配 chat 模型且未配 DASHSCOPE_API_KEY）")
        return bailian_client.complete(messages)

    judge_fn = make_judge_fn(_judge_complete) if (gw is not None or bailian_client is not None) else None

    # 精排（§3.2）：配了 rerank 模型才生效；.env 侧走**原生端点**（重排没有兼容格式）
    # 表里没有 rerank 模型 + 没有 .env 直连 → 两路皆空：装配一个恒等函数，
    # 让"没配重排"这件事在检索路径上表现为"原样返回候选"（而不是 None 把检索打断）
    rerank_fn = with_rerank_fallback(gw.make_rerank_fn() if gw is not None else None,
                                     bailian_rerank)
    mineru = MinerUClient(s.mineru_base_url) if s.mineru_base_url else None
    # 装配可见性（售后排查"后台配了模型为什么没生效"的第一手依据；测试也据此守护漏传）
    # 历史教训：vision_fn 曾在网关分支里算出来却没传进 create_app，视觉能力静默空转
    queue = None
    if s.queue_backend == "arq":
        from app.queue import ArqQueue

        queue = ArqQueue(redis_host=s.redis_host, redis_port=s.redis_port)
    # 重启收尾（评审遗留）：上次进程留下的 running 作业与孤儿 pending 文档必须说明白——
    # 否则界面永远显示"排队中"，而没有任何东西会去处理它。异步档只标作业、**绝不动文档**：
    # 那些 pending 正是排队等 worker 的任务，worker 是另一个进程，重启 backend 不影响它。
    rec = recover_interrupted_jobs(engine, has_external_worker=queue is not None)
    if rec["jobs"] or rec["documents"]:
        logging.getLogger("umax").warning(
            "启动收尾：%d 个后台作业标记为中断，%d 篇文档从「排队中」改为失败（可点重建/重试继续）",
            rec["jobs"], rec["documents"])
    if not s.admin_email or not s.admin_password:
        logging.getLogger("umax").warning(
            "ADMIN_EMAIL/ADMIN_PASSWORD 未配置：将按默认账号播种初始管理员，部署后必须登录改密")
    app = create_app(engine=engine, embedder=embedder, chat_fn=chat_fn,
                     upload_dir=upload_dir, mineru=mineru, queue=queue,
                     vision_fn=vision_fn, license_public_key=s.license_public_key,
                     license_file=str(s.license_file), settings_store=cfg_store,
                     judge_fn=judge_fn, rerank_fn=rerank_fn)
    app.state.wired = {"chat": chat_fn is not None, "embedder": embedder is not None,
                       "vision": vision_fn is not None, "mineru": mineru is not None,
                       "queue": queue is not None, "judge": judge_fn is not None,
                       "rerank": rerank_fn is not None,
                       "license_enforced": bool(s.license_public_key)}
    return app


def main() -> None:
    import os

    import uvicorn

    # 容器内必须绑 0.0.0.0 才能被反代/compose 网络访问；本机开发默认 127.0.0.1 不变
    uvicorn.run(build_production_app(), host=os.environ.get("UVICORN_HOST", "127.0.0.1"),
                port=int(os.environ.get("UVICORN_PORT", "8000")))


if __name__ == "__main__":
    main()
