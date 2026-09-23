# 端到端冒烟（-m smoke）：真百炼 embedder + 真 chat + 真 PG——验证 build_production_app 装配
# 日常回归不跑（pytest 默认 deselected）；改网关/配置后手动跑一次防接线错误
import pytest

from app.core.config import get_settings

pytestmark = pytest.mark.smoke


def test_full_chain_with_real_bailian(tmp_path, engine, db):
    """db fixture 负责建库+清表，engine 供装配。"""
    from fastapi.testclient import TestClient

    from app.main import build_production_app

    s = get_settings()
    assert s.dashscope_api_key, "backend/.env 缺 DASHSCOPE_API_KEY"
    app = build_production_app(upload_dir=str(tmp_path), engine=engine)
    client = TestClient(app)

    # 全员登录收口（任务 6）：build_production_app 在 users 空表时按 ADMIN_EMAIL/ADMIN_PASSWORD
    # 播种 admin——db 夹具清过表，故每次装配都会重播，建库前先拿会话
    r = client.post("/api/v1/auth/login",
                    json={"email": s.admin_email, "password": s.admin_password})
    assert r.status_code == 204, f"播种的初始管理员登录失败：{r.status_code} {r.text}"

    kb = client.post("/api/v1/kb", json={"name": "冒烟库"}).json()
    raw = (s.docs_dir / "rag_dirty_doc_01.txt").read_bytes()
    doc = client.post(f"/api/v1/kb/{kb['id']}/documents",
                      files={"file": ("rag_dirty_doc_01.txt", raw, "text/plain")}).json()
    assert doc["status"] == "ready", f"入库失败: {doc['error']}"

    resp = client.post("/api/v1/chat", json={
        "question": "生鲜支持七天无理由退货吗？", "kb_ids": [kb["id"]]}).json()
    assert "不支持" in resp["answer"], resp["answer"]
    assert resp["citations"], "应带引用出处"
    assert resp["usage"]["prompt_tokens"] > 0
