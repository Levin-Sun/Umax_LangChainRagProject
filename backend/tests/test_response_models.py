# 响应模型守卫：凡"会返回正文"的操作都必须声明 response_model。
#
# 评审发现：此前 **33 个返回正文的端点没有响应 schema** —— 契约只描述了请求与错误码，响应是空白，
# 于是前端只能手写类型 + `as` 强转，后端改一个字段名（size_bytes → bytes）不会有任何测试变红，
# 只会在浏览器里静默变成 undefined。这条守卫让"漏声明"变成红灯，而不是等前端出事故。
#
# 为什么查 spec 而不是查路由对象：`contracts/openapi.json` 是契约的唯一事实源，
# spec 里没有 schema，前端的类型就无从生成——查 spec 才等于查"前端能不能拿到类型"。
from app.main import create_app

# 204 本就无正文；202/201 有正文，必须声明（此前正是它们最容易被漏）
NO_BODY_CODES = {"204"}


def _untyped_operations() -> list[str]:
    spec = create_app(engine=None).openapi()
    missing: list[str] = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            codes = [c for c in op["responses"] if c.startswith("2")]
            if not codes or codes[0] in NO_BODY_CODES:
                continue
            schema = ((op["responses"][codes[0]].get("content") or {})
                      .get("application/json", {}).get("schema"))
            if not schema:
                missing.append(f"{method.upper()} {path}")
    return missing


def test_every_body_returning_operation_declares_a_response_schema():
    missing = _untyped_operations()
    assert missing == [], (
        "这些端点会返回正文却没有响应 schema（前端只能手写类型，改字段名无人报警）："
        f"{missing}")


def test_typed_response_surface_does_not_shrink():
    """棘轮：已声明的响应面只能涨不能跌（防止有人在重构里把 response_model 删了）。"""
    spec = create_app(engine=None).openapi()
    typed = 0
    for ops in spec["paths"].values():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            codes = [c for c in op["responses"] if c.startswith("2")]
            if not codes or codes[0] in NO_BODY_CODES:
                continue
            if ((op["responses"][codes[0]].get("content") or {})
                    .get("application/json", {}).get("schema")):
                typed += 1
    assert typed >= 44, f"有响应 schema 的操作只剩 {typed} 个（应为 44）"
