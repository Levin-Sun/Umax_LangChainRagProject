# 运行时配置中心（§D「配置驱动」的 DB 侧门面）：提示词/检索/切块参数后台可改、改完即生效。
# app_settings 是通用键值表，本模块是它的白名单门面——只有登记在 SPEC 里的键可读写，
# 单一事实源（SPEC）同时供运行时校验、契约 schema 生成与前端表单三处消费。
# 单字段约束进 pydantic 模型（→ spec，422 有声明）；跨字段关系 OpenAPI 表达不了，
# 不硬拒、不偷偷改值，改为响应 warnings 提示（见 chunk_min/chunk_target）。
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import AppSetting
from app.services.chat import SYSTEM_PROMPT, VISION_PROMPT

DEFAULT_MISS_ANSWER = "资料里没有相关内容，无法回答。"


@dataclass(frozen=True)
class Spec:
    kind: str                 # text / int / float / bool
    label: str                # 后台表单标签（中文）
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    help: str = ""


def build_spec(settings=None) -> dict[str, Spec]:
    """键表 + 约束。默认值取自 .env（Settings）——后台不改时行为与配置文件一致。"""
    from app.core.config import get_settings

    s = settings or get_settings()
    return {
        "chat_system_prompt": Spec("text", "问答系统提示词", max_length=4000,
                                   help="决定回答的语气、格式与引用规则；改完下一次提问即生效。"),
        "chat_miss_answer": Spec("text", "未命中回答", max_length=200,
                                 help="检索无结果或生成失败时的兜底话术。"),
        "vision_prompt": Spec("text", "识图提示词", max_length=1000,
                              help="传图提问时把图片转文字描述的指令。"),
        "recall_k": Spec("int", "召回条数", 1, 50,
                         help="混合检索两路各自召回上限（BM25 与向量各取前 N）。"),
        "rerank_top_n": Spec("int", "最终保留条数", 1, 20,
                             help="进入提示词的条数，直接影响引用数量与成本。"
                                  "配了 rerank 模型时=精排后保留的条数；没有 rerank 模型时"
                                  "=RRF 融合后的截断条数（两者都不改召回上限 recall_k）。"),
        "min_sim": Spec("float", "向量最低相似度", 0.0, 1.0,
                        help="低于该余弦相似度的向量召回被丢弃（未命中判据之一）。"),
        "doc_image_caption": Spec("bool", "文档图片转文字入库",
                                  help="开启后，文档内嵌的图表/照片由视觉模型转成文字描述一起进索引；"
                                       "关闭则只索引正文（需已配置视觉模型才有效果）。"),
        "quota_warn_ratio": Spec("float", "配额预警阈值", 0.05, 1.0,
                                 help="用户用量达到限额该比例时，界面出预警提示（0.8=80%）。"),
        "chunk_target": Spec("int", "切块目标长度", 100, 2000,
                             help="建库时锁定；改后需对文档重处理才生效。"),
        "chunk_min": Spec("int", "切块最小长度", 10, 500,
                          help="过短的尾块会并入前一块。"),
    }


class SettingsStore:
    """app_settings 的读写门面。无缓存：每请求现读＝改完即生效（同「改表即生效」哲学）。"""

    def __init__(self, engine, settings=None):
        self._engine = engine
        self.spec = build_spec(settings)
        from app.core.config import get_settings
        s = settings or get_settings()
        self._defaults = {
            "chat_system_prompt": SYSTEM_PROMPT,
            "chat_miss_answer": DEFAULT_MISS_ANSWER,
            "vision_prompt": VISION_PROMPT,
            "doc_image_caption": s.doc_image_caption,
            "quota_warn_ratio": s.quota_warn_ratio,
            "recall_k": s.recall_k,
            "rerank_top_n": s.rerank_top_n,
            "min_sim": s.min_sim,
            "chunk_target": s.chunk_target,
            "chunk_min": s.chunk_min,
        }

    def defaults(self) -> dict:
        return dict(self._defaults)

    def overrides(self) -> dict:
        with Session(self._engine) as s:
            return {r.key: r.value for r in s.query(AppSetting)
                    if r.key in self.spec and r.value is not None}   # 双保险：库里有历史脏键也不外泄

    def effective(self, overrides: dict | None = None) -> dict:
        eff = self.defaults()
        eff.update(overrides if overrides is not None else self.overrides())
        return eff

    def warnings(self, effective: dict) -> list[str]:
        out = []
        if effective["chunk_min"] > effective["chunk_target"]:
            out.append(f"chunk_min（{effective['chunk_min']}）大于 chunk_target"
                       f"（{effective['chunk_target']}）：短块会全部并入首块，建议 min ≤ target。")
        return out

    def snapshot(self) -> dict:
        ov = self.overrides()
        eff = self.effective(ov)
        return {"values": eff, "defaults": self.defaults(), "overridden": sorted(ov),
                "warnings": self.warnings(eff),
                "labels": {k: v.label for k, v in self.spec.items()},
                "help": {k: v.help for k, v in self.spec.items()}}

    def chunk_params(self, kb) -> dict:
        """切块参数：**建库时锁定的值优先**（§3.3「建库时锁定切分参数」），否则用当前生效值。

        这里是切块参数的**唯一事实源**：同步端点与 ARQ worker 都必须走它。
        真机踩过（代码评审发现）：worker 曾直接用 `.env` 的 `chunk_target`，于是
        QUEUE_BACKEND 从 sync 换成 arq 后，同一篇文档切块数就变了（实测 1 块 vs 3 块），
        "建库时锁定切分参数"这个承诺在异步档静默失效——同一语义有两条实现，迟早漂移。
        """
        c = self.effective()
        return {"chunk_target": (kb.chunk_target if kb and kb.chunk_target else c["chunk_target"]),
                "chunk_min": c["chunk_min"]}

    def put(self, session: Session, changes: dict, by: str) -> list[str]:
        """写入覆盖；value=None 表示抹掉覆盖回到默认。返回真正发生变化的键。

        入参已由 pydantic 模型按 SPEC 约束校验（类型/范围/长度），这里只管落库。
        """
        changed: list[str] = []
        for key, value in changes.items():
            row = session.get(AppSetting, key)
            current = row.value if row else None
            if value is None:
                if row is not None:
                    session.delete(row)
                    changed.append(key)
                continue
            if current == value:
                continue
            if row is None:
                session.add(AppSetting(key=key, value=value, updated_by=by))
            else:
                row.value, row.updated_by = value, by
            changed.append(key)
        return changed
