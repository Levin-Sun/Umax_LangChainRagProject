# 认证原语：scrypt 口令哈希（stdlib 零新依赖）/ 会话 token 对 / 登录限流（进程内，单机够用）
import base64
import hashlib
import hmac
import secrets
import time

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    # 任何格式问题都判 False（不抛）：坏哈希行不应把请求变成 500
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        want = base64.b64decode(hash_b64)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                             n=int(n), r=int(r), p=int(p), dklen=len(want))
        return hmac.compare_digest(got, want)
    except Exception:
        return False


def token_digest(plain: str) -> str:
    """会话 token 的落库形态：只存 SHA-256 hex，DB 泄露造不出可用 cookie。"""
    return hashlib.sha256(plain.encode()).hexdigest()


def new_session_token() -> tuple[str, str]:
    """(cookie 明文, 落库摘要)。"""
    plain = secrets.token_urlsafe(32)
    return plain, token_digest(plain)


class LoginThrottle:
    """同 key（email|ip）窗口内连续失败达上限即拒；成功清零。可注入 now（monotonic 秒）。"""

    def __init__(self, *, max_failures: int = 10, window_s: float = 900):
        self.max_failures, self.window_s = max_failures, window_s
        self._fails: dict[str, list[float]] = {}

    def blocked(self, key: str, *, now: float | None = None) -> bool:
        return len(self._recent(key, now if now is not None else time.monotonic())) >= self.max_failures

    def failure(self, key: str, *, now: float | None = None) -> None:
        t = now if now is not None else time.monotonic()
        self._fails[key] = self._recent(key, t) + [t]

    def success(self, key: str) -> None:
        self._fails.pop(key, None)

    def _recent(self, key: str, now: float) -> list[float]:
        stamps = [x for x in self._fails.get(key, []) if now - x < self.window_s]
        if stamps:
            self._fails[key] = stamps
        return stamps
