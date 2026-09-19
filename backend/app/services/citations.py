# 回答中的 [n] 引用编号 → 来源文档列表（迁自 stage0 generate.py）
import re


def parse_citations(answer: str, hits: list[dict]) -> list[str]:
    """编号从 1 开始对应 hits；越界编号忽略；去重按文档名排序。"""
    return sorted({
        hits[int(n) - 1]["doc_name"]
        for n in re.findall(r"\[(\d+)\]", answer)
        if n.isdigit() and 1 <= int(n) <= len(hits)
    })
