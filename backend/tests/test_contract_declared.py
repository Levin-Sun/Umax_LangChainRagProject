# 契约 fuzz 的前提：错误码必须出现在 spec 里，否则 schemathesis 把合法响应判成违约。
# 任务 6 整体重写为"双轨守卫"：全员登录后 401 声明面=受护端点全集，403 声明面=admin 面全集
# ——新增端点忘挂登录依赖/忘挂角色依赖，都会在对应手法轨上曝光（旧"admin 面=401 全集"已废）。
from pathlib import Path

import json

SPEC = json.loads((Path(__file__).resolve().parents[2] / "contracts/openapi.json")
                  .read_text(encoding="utf-8"))

# 全受护端点声明 401；admin 面加 403；逐端点业务码保留（400/404/415/503/429 按各端点实态）
EXPECTED = {
    ("/api/v1/kb", "post"): {"401", "403"},
    ("/api/v1/kb", "get"): {"401"},
    ("/api/v1/kb/{kb_id}/documents", "get"): {"401", "404"},
    ("/api/v1/kb/{kb_id}/documents", "post"): {"401", "403", "404", "415"},
    ("/api/v1/documents/{doc_id}", "get"): {"401", "404"},
    ("/api/v1/documents/{doc_id}", "patch"): {"401", "403", "404"},
    ("/api/v1/documents/{doc_id}/reprocess", "post"): {"401", "403", "404"},
    ("/api/v1/documents/{doc_id}/chunks", "get"): {"401", "404"},
    ("/api/v1/retrieve", "post"): {"401", "403"},
    ("/api/v1/chat", "post"): {"401", "403", "404"},
    ("/api/v1/conversations", "get"): {"401"},
    ("/api/v1/conversations/{conv_id}/messages", "get"): {"401", "404"},
    ("/api/v1/models", "get"): {"401", "403"},
    ("/api/v1/models", "post"): {"400", "401", "403", "503"},
    ("/api/v1/models/{model_id}", "patch"): {"400", "401", "403", "404", "503"},
    ("/api/v1/models/{model_id}", "delete"): {"401", "403", "503"},
    ("/api/v1/usage/summary", "get"): {"401", "403"},
    ("/api/v1/auth/login", "post"): {"401", "429"},
    ("/api/v1/auth/logout", "post"): {"401"},
    ("/api/v1/auth/me", "get"): {"401"},
    ("/api/v1/auth/change-password", "post"): {"401"},
    ("/api/v1/users", "get"): {"401", "403"},
    ("/api/v1/users", "post"): {"400", "401", "403"},
    ("/api/v1/users/{user_id}", "patch"): {"400", "401", "403", "404"},
    ("/api/v1/users/{user_id}/grants", "get"): {"400", "401", "403", "404"},
    ("/api/v1/users/{user_id}/grants", "put"): {"400", "401", "403", "404"},
    ("/api/v1/audit", "get"): {"401", "403"},
}

# 匿名可达端点：健康检查 + 登录本身（登录声明 401 是"邮箱或口令错误"，不是受护）
ANONYMOUS = {("/api/v1/health", "get"), ("/api/v1/auth/login", "post")}


def _all_declared(status: str) -> set[tuple[str, str]]:
    return {(p, m) for p, ms in SPEC["paths"].items()
            for m, r in ms.items() if status in r["responses"]}


def test_error_codes_declared():
    for (path, method), codes in EXPECTED.items():
        declared = set(SPEC["paths"][path][method]["responses"])
        assert codes <= declared, f"{method.upper()} {path} 缺 {codes - declared}"


# 轨一：登录面 = 声明 401 的端点全集（除匿名可达两枚）——新端点忘挂 get_user 即红
def test_login_surface_is_exactly_401_declared():
    login_surface = _all_declared("401") - ANONYMOUS
    assert login_surface == set(EXPECTED) - ANONYMOUS, (
        f"多挂/漏挂登录依赖：{login_surface ^ (set(EXPECTED) - ANONYMOUS)}")


# 轨二：admin 面 = 声明 403 的端点全集——新端点该管 admin 却只挂登录，或反过来，都会在这里曝光
def test_admin_surface_is_exactly_403_declared():
    admin_surface = _all_declared("403")
    assert admin_surface == {p for p, codes in EXPECTED.items() if "403" in codes}, (
        f"角色守护声明面漂移：{admin_surface ^ {p for p, c in EXPECTED.items() if '403' in c}}")


# 匿名端点保持零错误声明：/health 挂了鉴权或往它身上贴 4xx 都会破坏探活（部署健康检查依赖它）
def test_health_declares_no_errors():
    declared = set(SPEC["paths"]["/api/v1/health"]["get"]["responses"])
    assert declared == {"200"}, f"/health 声明面应为纯 200，实得 {declared}"
