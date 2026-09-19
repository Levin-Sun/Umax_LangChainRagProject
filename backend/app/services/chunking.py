# 段落合并切块（迁自 stage0 ingest.py，参数显式化）
def chunk_text(text: str, *, target: int, min_len: int) -> list[str]:
    """相邻段落拼到目标长度为止；超长段落优先按句号切；过短尾块并入前块。"""
    paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        candidate = (buf + "\n" + para).strip() if buf else para
        if len(candidate) <= target or not buf:
            buf = candidate
        else:
            chunks.append(buf)
            buf = para
        while len(buf) > target * 2:
            cut = buf.rfind("。", 0, target * 2)
            cut = cut + 1 if cut > 0 else target * 2
            chunks.append(buf[:cut])
            buf = buf[cut:]
    if buf:
        chunks.append(buf)
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < min_len:
            merged[-1] = merged[-1] + "\n" + c
        else:
            merged.append(c)
    return merged
