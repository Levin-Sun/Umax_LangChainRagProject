# 认证原语：scrypt 往返、篡改/坏格式安全拒绝、会话 token 对、登录限流窗口
import re
from app.services.auth import (LoginThrottle, hash_password, new_session_token,
                               token_digest, verify_password)


def test_hash_roundtrip_and_wrong_password():
    stored = hash_password("S3cret-Pass!")
    assert verify_password("S3cret-Pass!", stored)
    assert not verify_password("wrong", stored)


def test_hash_is_salted_and_format_self_describing():
    a, b = hash_password("same"), hash_password("same")
    assert a != b, "盐必须随机——同口令两次哈希不同"
    assert re.fullmatch(r"scrypt\$16384\$8\$1\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+", a)


def test_verify_rejects_tampered_and_garbage_hashes():
    stored = hash_password("good-pass-123")
    assert not verify_password("whatever", stored[:-5] + "XXXXX")
    for junk in ("", "not-a-hash", "scrypt$16384$8$1$xx", "md5$8$1$aaaa$bbbb"):
        assert not verify_password(junk, junk)


def test_new_session_token_pair():
    plain, digest = new_session_token()
    assert len(plain) >= 32 and plain != digest
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == token_digest(plain), "落库摘要 = 对明文重算，get_user 依赖此一致性"


def test_throttle_blocks_after_10_failures_and_resets_on_success():
    t = LoginThrottle(max_failures=10, window_s=900)
    for _ in range(9):
        t.failure("a@x.com|1.2.3.4")
        assert not t.blocked("a@x.com|1.2.3.4")
    t.failure("a@x.com|1.2.3.4")
    assert t.blocked("a@x.com|1.2.3.4")
    assert not t.blocked("b@x.com|1.2.3.4"), "键=邮箱|IP，互不牵连"
    t.success("a@x.com|1.2.3.4")
    assert not t.blocked("a@x.com|1.2.3.4"), "成功登录清零"


def test_throttle_window_expiry():
    t = LoginThrottle(max_failures=2, window_s=100)
    now = [1000.0]                          # 时钟可注入，窗口过期不靠真等
    for _ in range(2):
        t.failure("k", now=now[0])
    assert t.blocked("k", now=now[0])
    assert not t.blocked("k", now=now[0] + 101)
