# License 授权（§D）：厂商私钥签发、客户部署只带公钥验签。
# 授权文件自身即信任根——改库/改文件内容都会让签名失效，这是年费续费的真抓手。
# 校验每次请求现读文件（同「改表即生效」哲学）：续期换文件立即生效，无需重启服务。
import base64
import json
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def b64d(s: str) -> bytes:
    # 容忍 urlsafe 变体与缺失 padding：客户手抄粘贴容易丢尾，宽松解码只放宽格式不放宽签名
    s = "".join(s.split()).replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def canonical_bytes(payload: dict) -> bytes:
    """签名与验签两侧共用的确定性序列化：键序固定、无空白、非 ASCII 不转义。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def new_keypair() -> tuple[str, str]:
    """生成授权密钥对：(私钥 base64, 公钥 base64)。私钥只留厂商本地，绝不进客户部署。"""
    priv = Ed25519PrivateKey.generate()
    return (b64e(priv.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw,
                                    ser.NoEncryption())),
            b64e(priv.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)))


def sign_payload(payload: dict, private_key_b64: str) -> str:
    priv = Ed25519PrivateKey.from_private_bytes(b64d(private_key_b64))
    return b64e(priv.sign(canonical_bytes(payload)))


def verify_signature(payload: dict, signature: str, public_key_b64: str) -> bool:
    """任何异常（密钥/签名格式坏、验签不过）一律 False，不抛——坏授权不该把请求变 500。"""
    try:
        Ed25519PublicKey.from_public_bytes(b64d(public_key_b64)).verify(
            b64d(signature), canonical_bytes(payload))
        return True
    except Exception:
        return False


def issue_license(private_key_b64: str, *, customer: str, machine_fingerprint: str,
                  days: int = 365, license_key: str | None = None,
                  features: dict | None = None,
                  now: datetime | None = None) -> dict:
    """厂商侧签发：产出可直接落盘的授权文件内容（dict，写文件时 dump 即可）。"""
    now = now or datetime.now(timezone.utc)
    payload = {
        "license_key": license_key or f"UMX-{uuid.uuid4().hex[:12].upper()}",
        "customer": customer,
        "machine_fingerprint": machine_fingerprint,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "features": features or {},
    }
    return {"alg": "ed25519", "payload": payload,
            "signature": sign_payload(payload, private_key_b64)}


@lru_cache(maxsize=1)
def _os_fingerprint() -> str:
    """兜底指纹：macOS 取 IOPlatformUUID、Linux 取 /etc/machine-id，再退主机名+MAC。

    ⚠️ 容器部署下 machine-id 会随容器重建而变（授权会突然失效）——所以交付时应显式设置
    MACHINE_FINGERPRINT（见 .env.example），本条兜底只服务裸机/开发环境。
    """
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2].strip()
        else:
            mid = Path("/etc/machine-id")
            if mid.exists():
                v = mid.read_text().strip()
                if v:
                    return v
    except Exception:
        pass
    return f"{platform.node()}-{uuid.getnode():012x}"


def machine_fingerprint(env: dict | None = None) -> str:
    """当前机器指纹：显式 MACHINE_FINGERPRINT 优先（容器/交付场景），否则走 OS 兜底。"""
    env = os.environ if env is None else env
    explicit = (env.get("MACHINE_FINGERPRINT") or "").strip()
    return explicit or _os_fingerprint()


@dataclass
class LicenseStatus:
    enforced: bool                 # 是否启用校验（未配公钥＝开发模式，不拦）
    valid: bool                    # 当前是否可用于运营
    reason: str | None = None
    license_key: str | None = None
    customer: str | None = None
    issued_at: str | None = None
    expires_at: str | None = None
    days_left: int | None = None
    features: dict = field(default_factory=dict)
    machine_fingerprint: str = ""


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_license_status(path: str | Path, public_key_b64: str, *,
                        fingerprint: str | None = None,
                        now: datetime | None = None) -> LicenseStatus:
    """读授权文件并判定可用性。未配公钥＝开发模式（enforced=False、valid=True，不拦任何操作）。"""
    fp = fingerprint if fingerprint is not None else machine_fingerprint()
    now = now or datetime.now(timezone.utc)
    if not public_key_b64:
        return LicenseStatus(enforced=False, valid=True, machine_fingerprint=fp,
                             reason="未启用授权校验（开发模式）")
    p = Path(path)
    if not p.exists():
        return LicenseStatus(enforced=True, valid=False, machine_fingerprint=fp,
                             reason="未找到授权文件")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        payload = doc["payload"]
        signature = doc["signature"]
        assert isinstance(payload, dict) and isinstance(signature, str)
    except Exception:
        return LicenseStatus(enforced=True, valid=False, machine_fingerprint=fp,
                             reason="授权文件格式错误")
    base = dict(license_key=payload.get("license_key"), customer=payload.get("customer"),
                issued_at=payload.get("issued_at"), expires_at=payload.get("expires_at"),
                features=payload.get("features") or {}, machine_fingerprint=fp)
    if not verify_signature(payload, signature, public_key_b64):
        return LicenseStatus(enforced=True, valid=False, reason="授权文件签名无效", **base)
    exp = _parse_dt(payload.get("expires_at"))
    if exp is None:
        return LicenseStatus(enforced=True, valid=False, reason="授权期限字段不可解析", **base)
    days_left = (exp - now).days
    if exp <= now:
        return LicenseStatus(enforced=True, valid=False, days_left=days_left,
                             reason=f"授权已于 {exp.date().isoformat()} 到期，请联系服务商续期",
                             **base)
    bound = (payload.get("machine_fingerprint") or "").strip()
    if bound and bound != fp:
        return LicenseStatus(enforced=True, valid=False, days_left=days_left,
                             reason="授权绑定的机器与本机不符", **base)
    return LicenseStatus(enforced=True, valid=True, days_left=days_left, **base)
