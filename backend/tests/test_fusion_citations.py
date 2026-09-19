# TDD 红灯：RRF 融合与引用解析（纯函数，迁自 stage0 并补边界）
from app.services.citations import parse_citations
from app.services.fusion import rrf_fuse


def test_rrf_formula_matches_reciprocal_rank():
    fused = rrf_fuse([[7, 3, 5], [3, 7, 5]], k=60)
    assert fused[3] == 1 / 62 + 1 / 61   # 第一名与第二名各贡献一次
    assert fused[7] == 1 / 61 + 1 / 62
    assert fused[5] == 1 / 63 + 1 / 63
    assert sorted(fused, key=lambda i: -fused[i])[:2] == [3, 7] or \
        abs(fused[3] - fused[7]) < 1e-12  # 两者同分


def test_rrf_single_ranking_preserves_order():
    fused = rrf_fuse([[0, 1, 2]])
    assert fused[0] > fused[1] > fused[2]


def test_rrf_empty_rankings():
    assert rrf_fuse([]) == {}


def test_rrf_disjoint_sets_kept():
    fused = rrf_fuse([[1], [2]])
    assert set(fused) == {1, 2}
    assert fused[1] == fused[2]


def _hits(n):
    return [{"doc_name": f"d{i}.pdf"} for i in range(n)]


def test_citations_extract_and_map():
    answer = "退货需24小时响应[1]，生鲜不支持七天无理由[2]。"
    assert parse_citations(answer, _hits(3)) == ["d0.pdf", "d1.pdf"]


def test_citations_ignore_out_of_range_and_dedupe():
    answer = "见[2]与[5]，重复[2]，越界[9]。"
    assert parse_citations(answer, _hits(3)) == ["d1.pdf"]  # 5/9 越界忽略


def test_citations_none_returns_empty():
    assert parse_citations("没有引用编号", _hits(2)) == []
