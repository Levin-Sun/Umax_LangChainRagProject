# 并发路径（评审点名的盲区：此前零并发覆盖）。
#
# 为什么单测顺序调用发现不了问题：FastAPI 默认在**线程池**里跑同步端点，每个请求一个
# SQLAlchemy Session；而"看起来顺序执行"的测试永远碰不到"两个请求同时写同一批行"的情况。
# 这里用真线程 + 真 PG 打三类并发：并发问答、并发重建同一库、重建与上传同时发生。
# 判据不是"跑得快"，而是**都不失败、数据最终一致、没有死锁**。
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import BackgroundJob, Chunk, Document, User
from app.services.auth import hash_password, new_session_token
from app.services.crypto import encrypt_secret
from app.models import KnowledgeBase, ModelConfig
from tests.conftest import seed_user
from tests.test_api import FakeEmbedder

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
TEXT = "售后处理流程说明：客户申请退货先核对订单号，破损优先补发。" * 20


def _app(engine, tmp_path):
    return create_app(engine=engine, secret="k", embedder=FakeEmbedder(1024),
                      chat_fn=lambda q, hits: {"answer": "答 [1]", "prompt_tokens": 3,
                                               "completion_tokens": 2},
                      upload_dir=str(tmp_path), spawn=lambda fn: fn())


def _login_tokens(engine, tmp_path, n: int) -> list[str]:
    """直接造 n 个会话（免去 n 次登录限流），模拟 n 个并发用户。"""
    with Session(engine) as s:
        u = s.query(User).filter_by(email=ADMIN[0]).one()
        tokens = []
        for _ in range(n):
            plain, digest = new_session_token()
            from app.models import UserSession
            from datetime import datetime, timedelta, timezone
            s.add(UserSession(token_hash=digest, user_id=u.id,
                              expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
            tokens.append(plain)
        s.commit()
    return tokens


def _mk_kb_with_model(engine, tmp_path, docs=2) -> tuple[int, list[str]]:
    seed_user(engine, *ADMIN, role="admin")
    c = TestClient(_app(engine, tmp_path))
    c.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    kb = c.post("/api/v1/kb", json={"name": "并发库"}).json()
    for i in range(docs):
        c.post(f"/api/v1/kb/{kb['id']}/documents",
               files={"file": (f"c{i}.txt", TEXT.encode(), "text/plain")})
    with Session(engine) as s:
        s.add(ModelConfig(tenant_id="default", scenario="chat", provider="generic",
                          base_url="http://x/v1",
                          encrypted_api_key=encrypt_secret("sk", "k"), model_name="m"))
        s.commit()
    return kb["id"], _login_tokens(engine, tmp_path, 8)


def _clients_with_tokens(engine, tmp_path, tokens: list[str]) -> list[TestClient]:
    """每个"并发用户"一个 client，会话 cookie 设在 client 上（per-request cookies= 已弃用）。"""
    out = []
    for tok in tokens:
        c = TestClient(_app(engine, tmp_path))
        c.cookies.set("umax_session", tok)
        out.append(c)
    return out


def _run_parallel(targets: list) -> list:
    """同时起跑（barrier）并收集每个线程的结果/异常。"""
    barrier = threading.Barrier(len(targets))
    results: list[object] = [None] * len(targets)

    def wrap(i, fn):
        barrier.wait()          # 尽量同时进入临界区
        try:
            results[i] = fn()
        except Exception as exc:      # noqa: BLE001  收集异常就是本测试的判据
            results[i] = exc

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(targets)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_concurrent_chat_requests_all_succeed_with_own_conversations(engine, db, tmp_path):
    kb_id, tokens = _mk_kb_with_model(engine, tmp_path, docs=1)
    clients = _clients_with_tokens(engine, tmp_path, tokens)

    def ask(i):
        return clients[i].post("/api/v1/chat", json={"question": f"生鲜能退吗{i}",
                                                     "kb_ids": [kb_id]})

    results = _run_parallel([lambda i=i: ask(i) for i in range(8)])
    codes = [r.status_code if hasattr(r, "status_code") else repr(r) for r in results]
    assert codes == [200] * 8, f"并发问答必须全部成功，实得 {codes}"
    conv_ids = {r.json()["conversation_id"] for r in results}
    assert len(conv_ids) == 8, "每个请求各建各的会话，不该互相串"
    with Session(engine) as s:
        assert s.query(Chunk).filter_by(kb_id=kb_id).count() >= 1   # 数据未被并发写坏


def test_concurrent_reindex_on_same_kb_finishes_and_records_both_jobs(engine, db, tmp_path):
    """两个管理员同时点"重建"：不能死锁、不能互相把对方的结果写坏，两笔作业都要留下。"""
    kb_id, tokens = _mk_kb_with_model(engine, tmp_path, docs=2)
    clients = _clients_with_tokens(engine, tmp_path, tokens[:2])

    def rebuild(i):
        return clients[i].post("/api/v1/reindex", json={"kb_ids": [kb_id]})

    results = _run_parallel([lambda i=i: rebuild(i) for i in range(2)])
    assert [r.status_code for r in results] == [202, 202]
    with Session(engine) as s:
        jobs = s.query(BackgroundJob).filter_by(kind="reindex").all()
        assert len(jobs) == 2, "两次重建都该留下作业记录"
        assert all(j.status == "done" for j in jobs), [j.status for j in jobs]
        docs = s.query(Document).filter_by(kb_id=kb_id).all()
        assert all(d.status == "ready" for d in docs), [d.status for d in docs]
        for d in docs:      # 切块没有重复堆叠（并发重建最容易出的脏数据）
            n = s.query(Chunk).filter_by(document_id=d.id).count()
            assert n >= 1 and s.query(Chunk).filter_by(document_id=d.id,
                                                       chunk_index=n).count() == 0


def test_reindex_then_reprocess_same_document_does_not_corrupt_chunks(engine, db, tmp_path):
    """重建与单篇重处理同时打在同一个文档上：最终切块必须与服务端某一轮的结果一致（不叠不丢）。"""
    kb_id, tokens = _mk_kb_with_model(engine, tmp_path, docs=1)
    doc_id = None
    with Session(engine) as s:
        doc_id = s.query(Document).filter_by(kb_id=kb_id).one().id
    clients = _clients_with_tokens(engine, tmp_path, tokens[:2])

    results = _run_parallel([
        lambda: clients[0].post("/api/v1/reindex", json={"kb_ids": [kb_id]}),
        lambda: clients[1].post(f"/api/v1/documents/{doc_id}/reprocess"),
    ])
    assert all(hasattr(r, "status_code") and r.status_code in (200, 202) for r in results), results
    with Session(engine) as s:
        rows = s.query(Chunk).filter_by(document_id=doc_id).order_by(Chunk.chunk_index).all()
        assert [c.chunk_index for c in rows] == list(range(len(rows))), \
            "chunk_index 必须连续：叠了或丢了都会在这里露出来"
        assert len(rows) >= 1
