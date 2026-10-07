# 契约 fuzz 的前提：错误码必须出现在 spec 里，否则 schemathesis 把合法响应判成违约。
# 任务 6 建立"双轨守卫"（401=登录面、403=admin 面），首登强改密新增"轨三"：
# 428=受护端点全集减 auth 三件套（me/logout/change-password 豁免）——口令非本人设定的账号
# 未改密前除豁免端点外一律 428。三轨各自锁定一个收口维度：忘挂登录/角色/门闸都会在对应轨上曝光。
from pathlib import Path

import json

SPEC = json.loads((Path(__file__).resolve().parents[2] / "contracts/openapi.json")
                  .read_text(encoding="utf-8"))

# 全受护端点声明 401+428；admin 面加 403；逐端点业务码保留（400/404/415/503/429 按各端点实态）
_EXPECTED_BASE = {
    ("/api/v1/kb", "post"): {"401", "403"},
    ("/api/v1/kb", "get"): {"401"},
    ("/api/v1/kb/{kb_id}/documents", "get"): {"401", "404"},
    ("/api/v1/kb/{kb_id}/documents", "post"): {"401", "403", "404", "415"},
    ("/api/v1/documents/{doc_id}", "get"): {"401", "404"},
    ("/api/v1/documents/{doc_id}", "patch"): {"401", "403", "404"},
    ("/api/v1/documents/{doc_id}/reprocess", "post"): {"401", "403", "404"},
    ("/api/v1/documents/{doc_id}/chunks", "get"): {"401", "404"},
    ("/api/v1/retrieve", "post"): {"401", "403"},
    ("/api/v1/chat", "post"): {"400", "401", "403", "404", "429"},   # 429=用户级配额用尽
    ("/api/v1/conversations", "get"): {"401"},
    ("/api/v1/conversations/{conv_id}/messages", "get"): {"401", "404"},
    ("/api/v1/conversations/{conv_id}", "delete"): {"401", "404"},
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
    ("/api/v1/users/{user_id}", "delete"): {"400", "401", "403", "404"},
    ("/api/v1/users/{user_id}/grants", "get"): {"400", "401", "403", "404"},
    ("/api/v1/users/{user_id}/grants", "put"): {"400", "401", "403", "404"},
    ("/api/v1/audit", "get"): {"401", "403"},
    ("/api/v1/api-keys", "post"): {"400", "401", "403"},
    ("/api/v1/api-keys", "get"): {"401", "403"},
    ("/api/v1/api-keys/{key_id}", "patch"): {"400", "401", "403", "404"},
    ("/api/v1/api-keys/{key_id}", "delete"): {"401", "403", "404"},
    # 开放 API 兼容端点：Bearer key 认证（不走会话），401=无效 key，429=配额尽，400=无 user 消息
    ("/api/v1/openai/chat/completions", "post"): {"400", "401", "429"},
    # 用量视图：me=登录面（含未命中门闸豁免？否——首登未改密不得看用量），users=admin 面
    ("/api/v1/usage/me", "get"): {"401"},
    ("/api/v1/usage/users", "get"): {"401", "403"},
    # 配置中心：admin 面（GET 读生效值/默认值/覆盖清单；PUT 写，422 由动态模型声明）
    ("/api/v1/settings", "get"): {"401", "403"},
    ("/api/v1/settings", "put"): {"401", "403"},
    # 授权状态：admin 面只读（客户据此拿指纹申请授权；到期时写操作 403）
    ("/api/v1/license", "get"): {"401", "403"},
    # 白标：GET 匿名可读（登录页要显品牌，零错误声明）；PUT admin 面
    ("/api/v1/branding", "get"): set(),
    ("/api/v1/branding", "put"): {"400", "401", "403"},
}
# 首登门闸豁免集：me（前端靠它知道该弹改密框）/logout（随时可走人）/change-password（解除门闸
# 唯一通道）；login 不走 get_user，天然不在此门闸的声明面内
MUST_CHANGE_EXEMPT = {("/api/v1/auth/me", "get"), ("/api/v1/auth/logout", "post"),
                      ("/api/v1/auth/change-password", "post"), ("/api/v1/auth/login", "post")}
# 匿名可达端点：健康检查 + 登录本身 + 白标读取（登录页要显品牌）
ANONYMOUS = {("/api/v1/health", "get"), ("/api/v1/auth/login", "post"),
             ("/api/v1/branding", "get")}
# Bearer 认证例外面：开放 API 端点不做会话鉴权（客户系统没有浏览器 cookie），
# 401 语义是"无效 API key"——不参与登录面/门闸面轨，单独成轨守护
BEARER_AUTH = {("/api/v1/openai/chat/completions", "post")}
# 428 派生豁免 = 门闸三件套 ∪ Bearer 例外面 ∪ 匿名面（匿名端点不做会话门闸）
EXPECTED = {(p, m): codes | {"428"}
            if (p, m) not in MUST_CHANGE_EXEMPT | BEARER_AUTH | ANONYMOUS else codes
            for (p, m), codes in _EXPECTED_BASE.items()}

def _all_declared(status: str) -> set[tuple[str, str]]:
    return {(p, m) for p, ms in SPEC["paths"].items()
            for m, r in ms.items() if status in r["responses"]}


def test_error_codes_declared():
    for (path, method), codes in EXPECTED.items():
        declared = set(SPEC["paths"][path][method]["responses"])
        assert codes <= declared, f"{method.upper()} {path} 缺 {codes - declared}"


# 轨一：登录面 = 声明 401 的端点全集（除匿名两枚 + Bearer 例外面）——新端点忘挂 get_user 即红
def test_login_surface_is_exactly_401_declared():
    login_surface = _all_declared("401") - ANONYMOUS - BEARER_AUTH
    assert login_surface == set(EXPECTED) - ANONYMOUS - BEARER_AUTH, (
        f"多挂/漏挂登录依赖：{login_surface ^ (set(EXPECTED) - ANONYMOUS - BEARER_AUTH)}")


# 轨二：admin 面 = 声明 403 的端点全集——新端点该管 admin 却只挂登录，或反过来，都会在这里曝光
def test_admin_surface_is_exactly_403_declared():
    admin_surface = _all_declared("403")
    assert admin_surface == {p for p, codes in EXPECTED.items() if "403" in codes}, (
        f"角色守护声明面漂移：{admin_surface ^ {p for p, c in EXPECTED.items() if '403' in c}}")


# 匿名端点保持零错误声明：/health 挂了鉴权或往它身上贴 4xx 都会破坏探活（部署健康检查依赖它）
def test_health_declares_no_errors():
    declared = set(SPEC["paths"]["/api/v1/health"]["get"]["responses"])
    assert declared == {"200"}, f"/health 声明面应为纯 200，实得 {declared}"


# 轨三：首登门闸面 = 声明 428 的端点全集 = 受护端点全集减豁免三件套、减 Bearer 例外面——
# 新端点挂了登录却忘挂门闸（或在豁免端点上误挂 428），都在这里曝光
def test_must_change_surface_is_exactly_428_declared():
    gate_surface = _all_declared("428")
    want = {p for p in EXPECTED
            if p not in MUST_CHANGE_EXEMPT and p not in BEARER_AUTH and p not in ANONYMOUS}
    assert gate_surface == want, f"首登门闸声明面漂移：{gate_surface ^ want}"


# 轨四：Bearer 面——开放 API 端点必须自带 401（无效 key）声明，且不得挂 428/403（会话语义不适用）
def test_bearer_surface_declares_401_only():
    for p in BEARER_AUTH:
        declared = set(SPEC["paths"][p[0]][p[1]]["responses"])
        assert "401" in declared, f"{p} 缺 401（无效 API key）声明"
        assert not ({"403", "428"} & declared), f"{p} 混入了会话语义状态码"
