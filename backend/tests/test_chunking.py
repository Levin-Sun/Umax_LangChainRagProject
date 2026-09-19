# TDD 红灯：段落合并切块（stage0 chunk_text 的服务化升级，补齐边界用例）
from app.services.chunking import chunk_text


def test_short_doc_single_chunk():
    text = "第一段落。\n第二段落。"
    assert chunk_text(text, target=300, min_len=60) == ["第一段落。\n第二段落。"]


def test_paragraphs_merge_to_target():
    p1, p2, p3 = "甲" * 100, "乙" * 100, "丙" * 100
    chunks = chunk_text(f"{p1}\n{p2}\n{p3}", target=250, min_len=60)
    assert chunks == [f"{p1}\n{p2}", p3]


def test_oversized_paragraph_hard_split_on_period():
    """单段超 2*target 时按句号切；不足一个句号的按长度硬切。"""
    sents = "句子内容一二三。" * 45  # 360 字一段
    chunks = chunk_text(sents, target=150, min_len=60)
    assert len(chunks) >= 2
    assert all(c.endswith("。") for c in chunks)
    assert "".join(chunks) == sents


def test_oversized_paragraph_without_period():
    sents = "无标点长文本" * 60  # 360 字，无句号
    chunks = chunk_text(sents, target=150, min_len=60)
    assert "".join(chunks) == sents
    assert len(chunks) >= 2


def test_trailing_short_chunk_merged_back():
    big = "正" * 200
    tail = "尾注" * 10  # 20 字 < min_len
    chunks = chunk_text(f"{big}\n{tail}", target=250, min_len=60)
    assert chunks == [f"{big}\n{tail}"]


def test_crlf_and_blank_lines_normalized():
    chunks = chunk_text("第一段。\r\n\r\n  \r\n第二段。", target=300, min_len=60)
    assert chunks == ["第一段。\n第二段。"]


def test_every_char_preserved():
    text = "段落一。\n段落二内容更长一些。\n段落三。"
    rejoined = "".join(chunk_text(text, target=10, min_len=5))
    assert rejoined.replace("\n", "") == text.replace("\n", "")
