"""内置模型标本（厂商 → 接入档案 → 能力）的读取与自检。

为什么需要它：客户**不该被迫回答**"你的 embedding 走哪个端点""模型名到底带不带空格"这类
厂商实现细节——那是我们该内置的知识。真机教训（2026-10-09/10）：同一台机器上连着踩了
base_url 少 `/v1`、厂商只有对话没有向量、模型名写错、向量维度不匹配四个坑，全是"让用户填空"
造成的。

它是**数据不是代码**（app/data/model_catalog.json）：厂商的模型清单每月都在变，改数据文件就能
更新，不必动逻辑。三条原则写在这里，免得后来人把它做成硬编码：
  1. 标本只是"下拉选项的来源"，落库仍是 model_configs 那七个字段——零契约破坏，老配置不受影响。
  2. 标本**允许过期**：`verified=false` 的厂商在界面上如实标注"未实测"，且**永远保留手填入口**
     （自建模型、内网网关、厂商代理这些长尾一定存在）。
  3. 能力（chat/embedding/vision/rerank）**在这里就映射到场景**，所以登记时不必让客户选场景——
     场景留在系统内部当路由键（网关按它取模型），对外只暴露"用途"。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "model_catalog.json"

# 能力 → 场景（网关取模型时用的键）。两者目前一一对应，但刻意分开写：
# "能力"是给客户看的用途，"场景"是系统内部的路由键，将来一个能力对应多场景时不必改数据格式。
CAPABILITY_SCENARIOS = {"chat": "chat", "embedding": "embedding",
                        "vision": "vision", "rerank": "rerank"}


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    """读内置标本（带缓存；进程内不变，更新标本＝发新版镜像）。"""
    with open(CATALOG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def catalog_problems(catalog: dict | None = None) -> list[str]:
    """自检：标本形状不对时要在测试里先红，而不是等客户点开下拉才发现。

    检查的都是"错了会直接坑客户"的点：能力 key 是否认得、场景是否合法、
    接入档案是否存在、**向量维度是否与库里的列一致**（列写死 VECTOR(1024)）。
    """
    from app.core.config import get_settings

    cat = catalog if catalog is not None else load_catalog()
    dim_expected = get_settings().embedding_dim
    problems: list[str] = []
    seen: set[str] = set()
    for v in cat.get("vendors", []):
        vid = v.get("id")
        if not vid:
            problems.append("有厂商缺 id")
            continue
        if vid in seen:
            problems.append(f"厂商 id 重复：{vid}")
        seen.add(vid)
        profile_ids = {p.get("id") for p in v.get("profiles", [])}
        if not profile_ids:
            problems.append(f"{vid}: 没有接入档案")
        for p in v.get("profiles", []):
            if not p.get("base_url") and vid != "self":
                problems.append(f"{vid}/{p.get('id')}: 接入档案缺 base_url")
        for c in v.get("capabilities", []):
            key = c.get("key")
            if key not in CAPABILITY_SCENARIOS:
                problems.append(f"{vid}: 未知能力 {key}")
            if c.get("scenario") != CAPABILITY_SCENARIOS.get(key):
                problems.append(f"{vid}/{key}: scenario 与能力不匹配（应为 {CAPABILITY_SCENARIOS.get(key)}）")
            if c.get("profile") not in profile_ids:
                problems.append(f"{vid}/{key}: 引用了不存在的接入档案 {c.get('profile')}")
            if not c.get("model"):
                problems.append(f"{vid}/{key}: 缺模型名")
            if key == "embedding" and c.get("dim") != dim_expected:
                problems.append(f"{vid}/{key}: 维度 {c.get('dim')} ≠ 库里的 VECTOR({dim_expected})")
    return problems
