# 重排 provider（百炼原生 text-rerank，非 OpenAI 兼容格式）——§3.2 混合检索后的精排环节。
# 远程调用一律 MockTransport：真调用只留冒烟（跑真 key 的那条在交接记录里）。
import json

import httpx
import pytest

from app.services.rerank import RerankClient, RerankError

DOCS = ["打包胶带要多宽", "生鲜不支持七天无理由退货，坏果按比例赔偿", "亚马逊FBA时效"]


def _handler(*, scores=(0.01, 0.97, 0.02), status=200, body=None, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"code": "Throttling", "message": "限流"})
        if body is not None:
            return httpx.Response(200, json=body)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        return httpx.Response(200, json={
            "output": {"results": [{"index": i, "relevance_score": scores[i]} for i in order]},
            "usage": {"prompt_tokens": 176, "total_tokens": 176}})
    return handler


def _client(**kw):
    return RerankClient(api_key="sk-x", base_url="https://dashscope.aliyuncs.com/api/v1",
                        model="qwen3.7-text-rerank",
                        transport=httpx.MockTransport(_handler(**kw)), sleep=lambda s: None)


def test_rerank_sends_native_shape_and_returns_sorted_results():
    calls = []
    out = _client(calls=calls).rerank("生鲜能七天无理由退货吗", DOCS, top_n=2)
    req = calls[0]
    assert req.url.path == "/api/v1/services/rerank/text-rerank/text-rerank"
    assert req.headers["authorization"] == "Bearer sk-x"
    body = json.loads(req.content)
    assert body == {"model": "qwen3.7-text-rerank",
                    "input": {"query": "生鲜能七天无理由退货吗", "documents": DOCS},
                    "parameters": {"return_documents": False, "top_n": 2}}
    # 按分数降序：命中的第 2 篇必须排在最前（重排的意义就在这）
    assert [r["index"] for r in out["results"]] == [1, 2, 0]
    assert out["results"][0]["relevance_score"] == 0.97
    assert out["prompt_tokens"] == 176


def test_rerank_sorts_even_if_upstream_returns_unsorted():
    """不赌上游顺序：结果自己再排一次（上游换了实现顺序就变，契约要握在自己手里）。"""
    body = {"output": {"results": [{"index": 0, "relevance_score": 0.1},
                                   {"index": 1, "relevance_score": 0.9}]},
            "usage": {"total_tokens": 9}}
    out = _client(body=body).rerank("q", ["a", "b"])
    assert [r["index"] for r in out["results"]] == [1, 0]
    assert out["prompt_tokens"] == 9          # 只有 total_tokens 也要记上账


def test_rerank_retries_then_raises_with_clear_error():
    calls = []
    client = _client(status=429, calls=calls)
    with pytest.raises(RerankError) as e:
        client.rerank("q", DOCS)
    assert len(calls) == 4                    # 首次 + 3 次退避重试（与 ChatClient 同口径）
    assert "重排" in str(e.value)


def test_rerank_rejects_malformed_payloads():
    for body in ({}, {"output": {}}, {"output": {"results": []}},
                 {"output": {"results": [{"index": 9, "relevance_score": 0.5}]}},   # 越界索引
                 {"output": {"results": [{"relevance_score": 0.5}]}}):              # 缺 index
        with pytest.raises(RerankError):
            _client(body=body).rerank("q", DOCS)


def test_rerank_on_empty_documents_is_a_noop():
    """没有候选就别打接口（空请求既浪费钱又可能被判非法参数）。"""
    calls = []
    out = _client(calls=calls).rerank("q", [])
    assert out == {"results": [], "prompt_tokens": 0}
    assert calls == []


# ---- 产品路径接线：文档一直写着"混合检索+重排"，但端点从未把 rerank 传给 retrieve ----
# （2026-10-08 发现的落差）以下锁住"真的接上了"，以及"重排挂了不拖垮问答"。
from fastapi.testclient import TestClient   # noqa: E402
from sqlalchemy.orm import Session   # noqa: E402

from app.main import create_app   # noqa: E402
from app.models import Chunk, Document, KnowledgeBase, UsageRecord   # noqa: E402
from tests.conftest import login, seed_user   # noqa: E402
from tests.test_api import FakeEmbedder   # noqa: E402

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")


def _seed_two_docs(engine) -> list[int]:
    """两篇内容分明的文档，**带向量**（BM25 在两篇文档上会退化成全 0 分——实测，
    所以这条链路的召回由向量那一路兜住；顺序本身在这里不重要，测试断言的是"顺序被重排改变了"）。"""
    emb = FakeEmbedder(1024)
    with Session(engine) as s:
        kb = KnowledgeBase(tenant_id="default", name="重排库")
        s.add(kb)
        s.flush()
        texts = {"胶带.txt": "打包胶带要用多宽的多宽的胶带才够结实，仓储发货常用规格说明。",
                 "生鲜.txt": "生鲜不支持七天无理由退货，坏果按比例赔偿，破损优先补发。"}
        for name, text in texts.items():
            doc = Document(tenant_id="default", kb_id=kb.id, name=name, status="ready")
            s.add(doc)
            s.flush()
            s.add(Chunk(tenant_id="default", document_id=doc.id, kb_id=kb.id, chunk_index=0,
                        content=text, embedding=emb.embed([text])[0]))
        s.commit()
        return [kb.id]


def _api_client(engine, tmp_path, *, rerank_fn=None, chat_fn=None):
    return TestClient(create_app(engine=engine, secret="k-secret",
                                 embedder=FakeEmbedder(1024), chat_fn=chat_fn,
                                 upload_dir=str(tmp_path), rerank_fn=rerank_fn))


def test_retrieve_endpoint_uses_rerank_to_reorder_hits(engine, db, tmp_path):
    """反转顺序的假重排：结果必须跟着变——证明 /retrieve 真的把 rerank 传下去了。"""
    (kb_id,) = _seed_two_docs(engine)
    seed_user(engine, *ADMIN, role="admin")
    baseline = _api_client(engine, tmp_path)
    login(baseline, *ADMIN)
    before = baseline.post("/api/v1/retrieve", json={"query": "打包胶带要用多宽的，生鲜支持七天无理由退货吗", "kb_ids": [kb_id]}).json()

    c = _api_client(engine, tmp_path, rerank_fn=lambda q, cands: list(reversed(cands)))
    login(c, *ADMIN)
    after = c.post("/api/v1/retrieve", json={"query": "打包胶带要用多宽的，生鲜支持七天无理由退货吗", "kb_ids": [kb_id]}).json()
    assert len(before) >= 2, "前置不成立：基线召回为空，顺序断言无从谈起"
    assert [h["doc_name"] for h in after] == list(reversed([h["doc_name"] for h in before]))
    assert after[0]["doc_name"] != before[0]["doc_name"]      # 顺序真的被重排改了


def test_chat_citations_follow_rerank_order(engine, db, tmp_path):
    """编号 [n] 对应的是重排后的顺序：不接重排的话，引用编号解释的就是另一个顺序。"""
    (kb_id,) = _seed_two_docs(engine)
    seed_user(engine, *ADMIN, role="admin")
    base = _api_client(engine, tmp_path)
    login(base, *ADMIN)
    baseline = base.post("/api/v1/retrieve",
                         json={"query": "打包胶带要用多宽的，生鲜支持七天无理由退货吗", "kb_ids": [kb_id]}).json()
    assert len(baseline) >= 2

    c = _api_client(engine, tmp_path, rerank_fn=lambda q, cands: list(reversed(cands)),
                    chat_fn=lambda q, hits: {"answer": f"按 [1] 即 {hits[0]['doc_name']}",
                                             "prompt_tokens": 3, "completion_tokens": 2})
    login(c, *ADMIN)
    out = c.post("/api/v1/chat", json={"question": "打包胶带要用多宽的，生鲜支持七天无理由退货吗", "kb_ids": [kb_id]}).json()
    # 编号 [1] 解释的是**重排后**的第 1 篇（不接重排的话引用编号指向的是另一个顺序）
    assert out["citations"][0]["doc_name"] == baseline[-1]["doc_name"]
    assert out["cited_docs"] == [baseline[-1]["doc_name"]]


def test_rerank_failure_degrades_instead_of_failing_the_request(engine, db, tmp_path):
    """重排挂了不该让问答整个失败：降级为未重排顺序照常作答（与 chat 的口径刻意不同——
    chat 挂了就没有答案，重排挂了只是少一道精排）。"""
    (kb_id,) = _seed_two_docs(engine)
    seed_user(engine, *ADMIN, role="admin")

    def boom(query, cands):
        raise RuntimeError("重排模型 503")

    c = _api_client(engine, tmp_path, rerank_fn=boom,
                chat_fn=lambda q, hits: {"answer": "答[1]", "prompt_tokens": 1,
                                         "completion_tokens": 1})
    login(c, *ADMIN)
    assert c.post("/api/v1/retrieve",
                  json={"query": "打包胶带要用多宽的，生鲜支持七天无理由退货吗", "kb_ids": [kb_id]}).status_code == 200
    out = c.post("/api/v1/chat", json={"question": "打包胶带要用多宽的，生鲜支持七天无理由退货吗", "kb_ids": [kb_id]})
    assert out.status_code == 200 and out.json()["answer"] == "答[1]"


def test_rerank_usage_is_logged_to_the_ledger(engine, db, tmp_path):
    """重排是花钱的一步：网关侧记账（归检索链路）。这里用真网关 + MockTransport 端到端验一次。"""
    import httpx
    from app.models import ModelConfig
    from app.services.crypto import encrypt_secret
    from app.services.gateway import ModelGateway

    (kb_id,) = _seed_two_docs(engine)
    seed_user(engine, *ADMIN, role="admin")
    with Session(engine) as s:
        s.add(ModelConfig(tenant_id="default", scenario="rerank", provider="dashscope",
                          base_url="https://dashscope.aliyuncs.com/api/v1",
                          encrypted_api_key=encrypt_secret("sk-r", "g-secret"),
                          model_name="qwen3.7-text-rerank"))
        s.commit()
    gw = ModelGateway(engine, secret="g-secret", sleep=lambda s: None,
                      transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                          "output": {"results": [{"index": 0, "relevance_score": 0.9},
                                                 {"index": 1, "relevance_score": 0.1}]},
                          "usage": {"prompt_tokens": 77, "total_tokens": 77}})))
    c = _api_client(engine, tmp_path, rerank_fn=gw.make_rerank_fn())
    login(c, *ADMIN)
    assert c.post("/api/v1/retrieve",
                  json={"query": "打包胶带要用多宽的，生鲜支持七天无理由退货吗", "kb_ids": [kb_id]}).status_code == 200
    with Session(engine) as s:
        rows = s.query(UsageRecord).filter_by(scenario="rerank").all()
    assert len(rows) == 1 and rows[0].model == "qwen3.7-text-rerank"
    assert rows[0].prompt_tokens == 77 and rows[0].kb_id == kb_id


# ---- reorder：两条通路（网关/.env 直连）共用的唯一映射口径 ----
def test_reorder_keeps_unscored_candidates_and_attaches_scores():
    from app.services.rerank import reorder
    hits = [{"id": 1, "content": "a"}, {"id": 2, "content": "b"}, {"id": 3, "content": "c"}]
    out = reorder(hits, [{"index": 2, "relevance_score": 0.9},
                         {"index": 0, "relevance_score": 0.4}])
    assert [h["id"] for h in out] == [3, 1, 2]          # 未打分的排后面但不丢
    assert [h["rerank_score"] for h in out] == [0.9, 0.4, None]


def test_rerank_absent_is_a_passthrough_not_none(engine, db, tmp_path):
    """真机踩过并修掉的坑的回归测试：**没有任何重排通路时，检索必须照常返回候选**。

    起因：装配时复用了 chat 的 with_gateway_fallback——它在"表里没有该场景"时返回 None
    （chat 的语义是"没有答案"），而 retrieve() 会拿这个返回值切片 → /retrieve 直接 500。
    精排缺配置的正确表现是"没有这一步"，不是"没有结果"。
    """
    from app.main import with_rerank_fallback, with_rerank_degrade
    from app.services.gateway import GatewayError, NoProviderError

    hits = [{"id": 1, "content": "a"}, {"id": 2, "content": "b"}]
    assert with_rerank_fallback(None, None) is None

    def gw_none_provider(q, h):
        raise NoProviderError("表里没有 rerank 模型")

    # 只有网关这一路且表里没配：NoProviderError 被降级外壳接住 → 原样返回候选
    fn = with_rerank_degrade(with_rerank_fallback(gw_none_provider, None))
    assert fn("q", hits) == hits

    # 网关 + .env 两路：网关那路没配 → 交给 .env 直连
    direct = lambda q, h: [{"id": 2}, {"id": 1}]        # noqa: E731
    assert with_rerank_fallback(gw_none_provider, direct)("q", hits) == [{"id": 2}, {"id": 1}]

    # 配了但全挂（GatewayError）**不降级**：那是配置错误，要现形（与问答同口径）
    def gw_dead(q, h):
        raise GatewayError("全部 rerank 模型均调用失败")

    with pytest.raises(GatewayError):
        with_rerank_fallback(gw_dead, direct)("q", hits)
