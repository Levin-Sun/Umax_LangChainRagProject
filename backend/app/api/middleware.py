# 应用中间件（原 main.py 里的 _AllowHeaderMiddleware）。
from starlette.datastructures import MutableHeaders
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send


class _AllowHeaderMiddleware:
    """fuzz 修复⑤：同一路径由多个单方法路由拼成，Starlette 的 405/OPTIONS 只报首个路由的方法
    （AllowHeaderMismatch 抓到 Allow: POST 少了 GET，违反 RFC 9110）——合并为路径真实方法全集。"""

    def __init__(self, app: ASGIApp, routes) -> None:
        self.app, self.routes = app, routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        methods: set[str] = set()
        if scope["type"] == "http":
            for route in self.routes:
                allowed = getattr(route, "methods", None)
                if allowed:
                    matched = route.matches(scope)
                    match = matched[0] if isinstance(matched, tuple) else matched
                    if match in (Match.FULL, Match.PARTIAL):
                        methods |= allowed
        if not methods:
            await self.app(scope, receive, send)
            return

        async def send_with_allow(message: dict) -> None:
            if message["type"] == "http.response.start" and (
                    message["status"] == 405 or scope["method"] == "OPTIONS"):
                headers = MutableHeaders(raw=message["headers"])
                headers["allow"] = ", ".join(sorted(methods))
            await send(message)

        await self.app(scope, receive, send_with_allow)
