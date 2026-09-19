# TDD 红灯：混合检索服务（BM25+向量SQL+RRF），真 PG 测试库 + 确定性 fake embedder
import hashlib
import math

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Chunk, Document, KnowledgeBase
from app.services.retrieval import retrieve


class FakeEmbedder:
    """字符哈希词袋向量：内容越像余弦越高——测通路不测模型。"""

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for ch in t:
                h = int(hashlib.md5(ch.encode()).hexdigest(), 16) % self.dim
                v[h] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def _seed(db: Session, *, kb_name="kb1", docs: dict[str, list[str]], embedder=None):
    kb = KnowledgeBase(tenant_id="default", name=kb_name)
    db.add(kb)
    db.flush()
    for name, contents in docs.items():
        doc = Document(tenant_id="default", kb_id=kb.id, name=name, status="ready")
        db.add(doc)
        db.flush()
        for i, c in enumerate(contents):
            db.add(Chunk(
                tenant_id="default", document_id=doc.id, kb_id=kb.id,
                chunk_index=i, content=c,
                embedding=(embedder.embed([c])[0] if embedder else None),
            ))
    db.commit()
    return kb


@pytest.fixture
def embedder():
    return FakeEmbedder(get_settings().embedding_dim)


def test_bm25_ranks_keyword_match_first(db: Session, embedder):
    _seed(db, embedder=embedder, docs={
        "ops_return.pdf": ["生鲜类商品不支持七天无理由退货，坏果按比例赔偿"],
        "logistics.pdf": ["干线物流时效为48小时发货，大促期间可能延迟"],
        "pricing.pdf": ["阶梯定价：满100减10，满200减30"],
    })
    hits = retrieve(db, "生鲜 退货 政策", embedder=embedder, top_k=3)
    assert hits[0]["doc_name"] == "ops_return.pdf"
    assert hits[0]["bm25_hit"] is True


def test_vector_path_flags_and_null_embedding_survives(db: Session, embedder):
    """无向量的块（--no-embed 入库/新上传）仍能被 BM25 召回，不被向量 SQL 排除报错。"""
    _seed(db, docs={"a.pdf": ["唯一一块没有向量的内容 关键词甲"]}, embedder=None)
    hits = retrieve(db, "关键词甲", embedder=embedder, top_k=5)
    assert hits and hits[0]["doc_name"] == "a.pdf"


def test_kb_filter_scopes_results(db: Session, embedder):
    kb1 = _seed(db, kb_name="库一", docs={"in.pdf": ["目标内容 独有关键词丙丙"]}, embedder=embedder)
    _seed(db, kb_name="库二", docs={"out.pdf": ["目标内容 独有关键词丙丙"]}, embedder=embedder)
    hits = retrieve(db, "独有关键词丙丙", embedder=embedder, kb_ids=[kb1.id], top_k=10)
    assert {h["doc_name"] for h in hits} == {"in.pdf"}


def test_top_k_limit_and_hit_schema(db: Session, embedder):
    kb = _seed(db, embedder=embedder, docs={
        f"d{i}.pdf": [f"通用内容 通用内容 差异词{i}"] for i in range(8)
    })
    hits = retrieve(db, "通用内容", embedder=embedder, kb_ids=[kb.id], top_k=3)
    assert len(hits) == 3
    for h in hits:
        assert {"id", "doc_name", "chunk_index", "content", "score",
                "bm25_hit", "vec_hit"} <= set(h)
        assert h["score"] > 0


def test_reranker_hook_applied_last(db: Session, embedder):
    _seed(db, embedder=embedder, docs={
        "x.pdf": ["电商运营 笔记 关键词丁"], "y.pdf": ["电商仓储 关键词丁 笔记"],
    })
    hits = retrieve(db, "关键词丁 笔记", embedder=embedder, top_k=2,
                    rerank=lambda q, cands: list(reversed(cands)))
    assert [h["doc_name"] for h in hits] == list(
        reversed([h["doc_name"] for h in retrieve(
            db, "关键词丁 笔记", embedder=embedder, top_k=2)])
    )
