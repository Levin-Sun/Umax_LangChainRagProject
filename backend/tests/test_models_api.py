# 模型后台管理 API（§C 后台换模型/BYO-key 打码）+ 用量看板简版
# 阶段 2 起 models/usage 是 admin 面：夹具统一播种 admin 账号并登录
import hashlib
import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.main import create_app
from app.models import ModelConfig, UsageRecord
from app.services.crypto import decrypt_secret, encrypt_secret
from tests.conftest import login, seed_user

SECRET = "gateway-master"
ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


class FakeEmbedder:
    def __init__(self, dim):
        self.dim = dim

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for ch in t:
                v[int(hashlib.md5(ch.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@pytest.fixture
def client(engine, db, tmp_path):
    app = create_app(engine=engine, secret=SECRET,
                     embedder=FakeEmbedder(get_settings().embedding_dim),
                     upload_dir=str(tmp_path))
    with TestClient(app) as c:
        seed_user(engine, *ADMIN, role="admin")
        login(c, *ADMIN)
        yield c


@pytest.fixture
def client_no_secret(engine, db, tmp_path):
    app = create_app(engine=engine, secret="", embedder=None, upload_dir=str(tmp_path))
    with TestClient(app) as c:
        seed_user(engine, *ADMIN, role="admin")
        login(c, *ADMIN)
        yield c


MODEL_BODY = {"scenario": "chat", "provider": "deepseek",
              "base_url": "https://api.deepseek.com/v1",
              "api_key": "sk-plain-ABCDEFG1234", "model_name": "deepseek-chat",
              "fallback_rank": 0}


def _mask_fields(item):
    assert "api_key" not in item
    assert "encrypted_api_key" not in item


# ---- CRUD 与打码 ----
def test_create_model_config_encrypts_and_masks(client, db):
    r = client.post("/api/v1/models", json=MODEL_BODY)
    assert r.status_code == 201
    body = r.json()
    _mask_fields(body)
    assert body["api_key_masked"].endswith("1234") and "****" in body["api_key_masked"]
    assert "sk-plain-ABCDEFG1234" not in r.text  # 明文绝不回传
    row = db.query(ModelConfig).one()
    assert row.encrypted_api_key != "sk-plain-ABCDEFG1234"
    assert decrypt_secret(row.encrypted_api_key, SECRET) == "sk-plain-ABCDEFG1234"


def test_list_models_masked(client):
    client.post("/api/v1/models", json=MODEL_BODY)
    client.post("/api/v1/models", json={**MODEL_BODY, "model_name": "emb",
                                     "scenario": "embedding", "api_key": "sk-emb-ZZZ9"})
    items = client.get("/api/v1/models").json()
    assert {i["model_name"] for i in items} == {"deepseek-chat", "emb"}
    for i in items:
        _mask_fields(i)
    assert "sk-emb-ZZZ9" not in client.get("/api/v1/models").text


def test_patch_disable_and_rotate_key(client, db):
    mid = client.post("/api/v1/models", json=MODEL_BODY).json()["id"]
    r = client.patch(f"/api/v1/models/{mid}", json={"enabled": False, "api_key": "sk-new-XY88"})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert r.json()["api_key_masked"].endswith("XY88")
    row = db.get(ModelConfig, mid)
    assert decrypt_secret(row.encrypted_api_key, SECRET) == "sk-new-XY88"


def test_delete_model(client, db):
    mid = client.post("/api/v1/models", json=MODEL_BODY).json()["id"]
    assert client.delete(f"/api/v1/models/{mid}").status_code == 204
    assert client.get("/api/v1/models").json() == []


def test_reject_unknown_scenario(client):
    assert client.post("/api/v1/models", json={**MODEL_BODY, "scenario": "sms"}).status_code == 400


def test_requires_gateway_secret(client_no_secret):
    r = client_no_secret.post("/api/v1/models", json=MODEL_BODY)
    assert r.status_code == 503
    assert "GATEWAY_SECRET" in r.text


# ---- 用量看板简版：按 场景+模型 汇总 ----
def test_usage_summary_aggregates(client, db):
    db.add_all([
        UsageRecord(user_email="a@x", scenario="chat", model="m1", prompt_tokens=100, completion_tokens=20),
        UsageRecord(user_email="b@x", scenario="chat", model="m1", prompt_tokens=50, completion_tokens=10),
        UsageRecord(user_email="a@x", scenario="chat", model="m2", prompt_tokens=30, completion_tokens=5),
        UsageRecord(user_email="s@x", scenario="embedding", model="e1", prompt_tokens=400, completion_tokens=0),
    ])
    db.commit()
    rows = {(r["scenario"], r["model"]): r for r in client.get("/api/v1/usage/summary").json()}
    assert rows[("chat", "m1")] == {"scenario": "chat", "model": "m1", "calls": 2,
                                    "prompt_tokens": 150, "completion_tokens": 30}
    assert rows[("embedding", "e1")]["calls"] == 1
    assert set(rows) == {("chat", "m1"), ("chat", "m2"), ("embedding", "e1")}


# ---- 问答端点与网关台账的配合（logged 约定退役后：记账单点在端点）----
def _ask(client):
    kb = client.post("/api/v1/kb", json={"name": "k"}).json()
    client.post(f"/api/v1/kb/{kb['id']}/documents",
                files={"file": ("u.txt", "生鲜退货政策。".encode(), "text/plain")})
    return client.post("/api/v1/chat", json={"question": "生鲜 退货 政策", "kb_ids": [kb["id"]]})


def _logged_client(engine, db, tmp_path, fn):
    app = create_app(engine=engine, chat_fn=fn, upload_dir=str(tmp_path))
    c = TestClient(app)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    return c


def test_chat_usage_records_model_returned_by_chat_fn(engine, db, tmp_path):
    def fn(q, hits):
        return {"answer": "ok[1]", "prompt_tokens": 5, "completion_tokens": 2,
                "model": "glm-4-flash", "latency_ms": 42}
    c = _logged_client(engine, db, tmp_path, fn)
    assert _ask(c).status_code == 200
    rec = db.query(UsageRecord).one()  # 端点记账：记网关实际用成的模型，不是 .env 里那个
    assert (rec.model, rec.prompt_tokens, rec.latency_ms) == ("glm-4-flash", 5, 42)


def test_chat_records_once_even_if_chat_fn_claims_logged(engine, db, tmp_path):
    """logged 约定整体退役：网关不再自行记账，端点恒记一次——
    chat_fn 返回 logged 键（旧网关形态）也不得让端点漏记或双记。"""
    def fn(q, hits):
        return {"answer": "ok[1]", "prompt_tokens": 5, "completion_tokens": 2,
                "model": "m-g", "logged": True}
    c = _logged_client(engine, db, tmp_path, fn)
    _ask(c)
    rec = db.query(UsageRecord).one()
    assert (rec.user_email, rec.model) == (ADMIN[0], "m-g")
