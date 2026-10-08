# 路径/请求体层的字面常量与上限（原散落在 main.create_app 内部，拆 router 时收拢到一处）。
#
# 为什么值得单独一个模块：同一份限制会被**三处**消费——契约 schema（进 spec）、端点校验
# （运行时判定）、前端表单（提示与禁用态）。散着写就会漂移（历史上 LOGO_RE 同时被白标与
# 传图两处用，一旦各自复制一份正则，"界面说 400KB、后端放 300KB"这种事没人会发现）。
import re

# ---- 会话 ----
SESSION_COOKIE, SESSION_TTL_DAYS = "umax_session", 7
# 首登强改密（初始化向导收尾）：口令非本人设定的账号未改密前，全部受护端点 428——
# 与 403 分轨（403 专属 admin 面，双轨守卫不被稀释），豁免仅 auth 三件套：
# me（前端靠它知道该弹改密框）/logout（随时可走人）/change-password（解除门闸的唯一通道）
MUST_CHANGE_EXEMPT = ("/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/change-password")

# ---- 上传 ----
BATCH_MAX = 20   # 单批文件数上限：挡住"一次传一个文件夹"把单请求打成长时间占用

# ---- 白标（§2.2）----
DEFAULT_BRAND_NAME = "Umax RAG"
LOGO_RE = re.compile(r"^data:image/(?:png|jpeg|jpg|webp|gif|svg\+xml);base64,[A-Za-z0-9+/=]+$")
LOGO_MAX = 400_000   # base64 字符数上限 ≈ 300KB 二进制，防把 DB 当图床

# ---- 传图提问（阶段 2 放开）：data URL 直存消息 content part（与白标 logo 同闸：类型白名单+大小上限）
IMAGE_PATTERN = r"^data:image/(?:png|jpeg|jpg|webp|gif|svg\+xml);base64,"
IMAGE_MAX = 2_000_000        # 单张 base64 字符上限 ≈ 1.5MB 二进制
IMAGE_COUNT_MAX = 3

# ---- 邮箱格式约束进 spec（pattern 自动入 schema，422 由 FastAPI 默认声明）：
# 要求 @ 后至少带一个点分的域名标签（"a@x"、"a@.com" 都拒），+ 号路由标签放行
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
