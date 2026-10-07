# License 授权（§D）：厂商私钥签发 + 客户公钥验签；到期/机器不符/被篡改一律无效。
# 未配公钥＝开发模式不拦（既有测试与本地开发零影响）；配了才启用强制。
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.license import (b64d, canonical_bytes, issue_license, load_license_status,
                                  machine_fingerprint, new_keypair, sign_payload,
                                  verify_signature)
from tests.conftest import login, seed_user

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
FP = "fp-delivery-001"


def _write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "license.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------- 服务层：指纹 / 签发 / 验签 ----------------

def test_machine_fingerprint_prefers_env_then_os(monkeypatch):
    assert machine_fingerprint({"MACHINE_FINGERPRINT": " fp-x  "}) == "fp-x"
    fallback = machine_fingerprint({})
    assert fallback and fallback == machine_fingerprint({})   # 同机稳定


def test_sign_and_verify_roundtrip():
    priv, pub = new_keypair()
    payload = {"a": 1, "客户": "中文值", "list": [1, 2]}
    sig = sign_payload(payload, priv)
    assert verify_signature(payload, sig, pub) is True
    # 键序无关（canonical 序列化保证）
    assert verify_signature({"list": [1, 2], "客户": "中文值", "a": 1}, sig, pub) is True


def test_tampered_payload_and_wrong_key_rejected():
    priv, pub = new_keypair()
    _, other_pub = new_keypair()
    payload = {"customer": "甲"}
    sig = sign_payload(payload, priv)
    assert verify_signature({**payload, "customer": "乙"}, sig, pub) is False   # 改内容
    assert verify_signature(payload, sig, other_pub) is False                   # 换公钥
    assert verify_signature(payload, "not-base64!!", pub) is False               # 坏签名不抛
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'            # 键序固定、无空白


def test_issue_license_shape_and_verifiability():
    priv, pub = new_keypair()
    doc = issue_license(priv, customer="测试客户", machine_fingerprint=FP, days=30, now=NOW)
    assert doc["alg"] == "ed25519"
    assert doc["payload"]["license_key"].startswith("UMX-")
    assert doc["payload"]["expires_at"].startswith("2026-11-06")
    assert verify_signature(doc["payload"], doc["signature"], pub) is True


# ---------------- 服务层：状态判定 ----------------

def test_not_enforced_when_public_key_absent(tmp_path):
    st = load_license_status(tmp_path / "缺失.json", "", fingerprint=FP, now=NOW)
    assert st.enforced is False and st.valid is True
    assert "开发模式" in st.reason and st.machine_fingerprint == FP


def test_missing_file_and_malformed_file(tmp_path):
    priv, pub = new_keypair()
    assert "未找到授权文件" in load_license_status(tmp_path / "nope.json", pub,
                                                fingerprint=FP, now=NOW).reason
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert "格式错误" in load_license_status(bad, pub, fingerprint=FP, now=NOW).reason
    bad.write_text(json.dumps({"payload": "字符串不是对象", "signature": "x"}), encoding="utf-8")
    assert "格式错误" in load_license_status(bad, pub, fingerprint=FP, now=NOW).reason


def test_valid_license_status_fields(tmp_path):
    priv, pub = new_keypair()
    p = _write(tmp_path, issue_license(priv, customer="星辰科技", machine_fingerprint=FP,
                                       days=365, features={"max_docs": 5000}, now=NOW))
    st = load_license_status(p, pub, fingerprint=FP, now=NOW)
    assert st.valid and st.enforced
    assert st.customer == "星辰科技" and st.days_left == 365
    assert st.features == {"max_docs": 5000} and st.reason is None


def test_expired_and_bound_to_other_machine(tmp_path):
    priv, pub = new_keypair()
    p = _write(tmp_path, issue_license(priv, customer="甲", machine_fingerprint=FP,
                                       days=10, now=NOW - timedelta(days=40)))
    st = load_license_status(p, pub, fingerprint=FP, now=NOW)
    assert not st.valid and "到期" in st.reason and st.days_left < 0
    # 未过期但机器不符
    p = _write(tmp_path, issue_license(priv, customer="甲", machine_fingerprint="别的机器",
                                       days=100, now=NOW))
    st = load_license_status(p, pub, fingerprint=FP, now=NOW)
    assert not st.valid and "机器与本机不符" in st.reason


def test_signature_tamper_detected_via_file(tmp_path):
    priv, pub = new_keypair()
    doc = issue_license(priv, customer="甲", machine_fingerprint=FP, days=100, now=NOW)
    doc["payload"]["expires_at"] = "2099-01-01T00:00:00+00:00"   # 客户手改期限想续命
    st = load_license_status(_write(tmp_path, doc), pub, fingerprint=FP, now=NOW)
    assert not st.valid and "签名无效" in st.reason


# ---------------- 端点：状态查询 + 写操作门闸 ----------------

def _client(engine, tmp_path, *, pub="", license_path=None):
    return TestClient(create_app(engine=engine, secret="lic-secret", embedder=None,
                                 chat_fn=lambda q, h: {"answer": "a", "prompt_tokens": 1,
                                                       "completion_tokens": 1},
                                 upload_dir=str(tmp_path),
                                 license_public_key=pub,
                                 license_file=str(license_path) if license_path else None,
                                 machine_fingerprint=FP))


def test_license_endpoint_admin_only_reports_fingerprint(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    assert c.get("/api/v1/license").status_code == 401          # 匿名不可见
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    body = c.get("/api/v1/license").json()
    assert body["enforced"] is False and body["valid"] is True
    assert body["machine_fingerprint"] == FP                     # 客户据此申请授权
    assert "开发模式" in body["reason"]


def test_enforcement_blocks_admin_writes_but_keeps_reads_and_chat(engine, db, tmp_path):
    priv, pub = new_keypair()
    expired = _write(tmp_path, issue_license(priv, customer="甲", machine_fingerprint=FP,
                                             days=5, now=NOW - timedelta(days=60)))
    c = _client(engine, tmp_path, pub=pub, license_path=expired)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    # 读与问答照常（到期=只读，不是停摆）
    assert c.get("/api/v1/license").json()["valid"] is False
    assert c.get("/api/v1/kb").status_code == 200
    assert c.get("/api/v1/models").status_code == 200
    assert c.get("/api/v1/audit").status_code == 200
    # 写操作被拦，文案说清为什么
    r = c.post("/api/v1/kb", json={"name": "新库"})
    assert r.status_code == 403 and "到期" in r.json()["detail"]
    assert c.post("/api/v1/users", json={"email": "n@x.com", "name": "n",
                                        "password": "Passw0rd-1"}).status_code == 403
    assert c.put("/api/v1/branding", json={"brand_name": "x"}).status_code == 403
    assert c.post("/api/v1/api-keys", json={"name": "k"}).status_code == 403
    # 自助改密不受影响（口令安全不能被授权绑架）
    assert c.post("/api/v1/auth/change-password",
                  json={"old_password": ADMIN[1], "new_password": "New-Pass-9"}).status_code == 204


def test_valid_license_allows_writes_and_renewal_takes_effect_without_restart(engine, db, tmp_path):
    priv, pub = new_keypair()
    lic = _write(tmp_path, issue_license(priv, customer="星辰", machine_fingerprint=FP,
                                         days=30, now=NOW - timedelta(days=60)))   # 先过期
    c = _client(engine, tmp_path, pub=pub, license_path=lic)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    assert c.post("/api/v1/kb", json={"name": "库"}).status_code == 403
    # 续期：换文件即生效，不重启进程（每请求现读的收益）
    lic.write_text(json.dumps(issue_license(priv, customer="星辰", machine_fingerprint=FP,
                                            days=365)), encoding="utf-8")
    assert c.get("/api/v1/license").json()["valid"] is True
    assert c.post("/api/v1/kb", json={"name": "库"}).status_code == 201


# ---------------- 签发脚本（厂商工具）：产出能被验签器接受的文件 ----------------
def test_issue_cli_keygen_and_issue_roundtrip(tmp_path):
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "issue_license.py"
    kg = subprocess.run([sys.executable, str(script), "keygen"],
                        capture_output=True, text=True, check=True)
    priv = [l.split(":", 1)[1].strip() for l in kg.stdout.splitlines() if l.startswith("私钥")][0]
    pub = [l.split(":", 1)[1].strip() for l in kg.stdout.splitlines() if l.startswith("公钥")][0]
    out = tmp_path / "license.json"
    subprocess.run([sys.executable, str(script), "issue", "--private-key", priv,
                    "--customer", "星辰科技", "--fingerprint", FP, "--days", "30",
                    "--out", str(out)], capture_output=True, text=True, check=True)
    st = load_license_status(out, pub, fingerprint=FP)
    assert st.valid and st.customer == "星辰科技" and st.days_left in (29, 30)


# ---------------- 装配守护：生产装配不得漏传（vision_fn 曾静默空转） ----------------
def test_production_wiring_reports_all_scenarios(engine, db, tmp_path, monkeypatch):
    from app.models import ModelConfig
    from app.services.crypto import encrypt_secret
    from sqlalchemy.orm import Session

    from app.main import build_production_app
    from app.core.config import get_settings

    secret = "wire-secret-32bytes"
    monkeypatch.setenv("GATEWAY_SECRET", secret)
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", "some-pub-key")
    get_settings.cache_clear()
    with Session(engine) as s:
        for sc in ("chat", "embedding", "vision"):
            s.add(ModelConfig(tenant_id="default", scenario=sc, provider="openai",
                              base_url="http://127.0.0.1:9/v1", model_name=f"{sc}-m",
                              encrypted_api_key=encrypt_secret("sk-x", secret)))
        s.commit()
    app = build_production_app(upload_dir=str(tmp_path), engine=engine)
    assert app.state.wired == {"chat": True, "embedder": True, "vision": True,
                               "mineru": False, "queue": False, "license_enforced": True}
    get_settings.cache_clear()
