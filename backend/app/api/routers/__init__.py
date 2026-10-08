# 端点分组：一个模块一个业务域，各自暴露 build_router(rt) -> APIRouter。
#
# 为什么是"工厂函数"而不是模块级 router：端点里要用的 engine/cfg/queue/spawn 都是
# **每个 app 实例一份**的（create_app 的参数），模块级 router 只能靠全局变量传，
# 那样多实例（测试并行、同进程起两个 app）就会互相串。工厂把实例依赖变成闭包。
#
# BUILDERS 的顺序不重要（路径不重叠，FastAPI 按路径结构匹配而非注册顺序），
# 这里按"读代码从哪读起"排：系统面 → 鉴权 → 账号 → 知识库/文档 → 配置/用量 →
# 检索问答 → 模型 → 开放 API → 白标 → 评测 → 作业。
from app.api.routers import (auth, branding, documents, eval, jobs, kb, models_admin, open_api,
                             retrieval, settings_admin, system, usage, users)

BUILDERS = (
    system.build_router,
    auth.build_router,
    users.build_router,
    kb.build_router,
    documents.build_router,
    settings_admin.build_router,
    usage.build_router,
    retrieval.build_router,
    models_admin.build_router,
    open_api.build_router,
    branding.build_router,
    eval.build_router,
    jobs.build_router,
)
