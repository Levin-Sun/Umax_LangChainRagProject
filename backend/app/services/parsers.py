# 解析器注册表（§3.2-③"解析服务独立成模块可替换"）
# txt/md 直读；Office 三件套结构化抽取；PDF 文字层抽取，扫描件路由 MinerU
import io
from collections.abc import Callable

import httpx

TEXT_EXTS = {".txt", ".md"}
SCANNED_PAGE_RATIO = 0.5  # 无文字层页占比过半 → 判为扫描件


class ScannedPdfError(Exception):
    pass


def supported_extensions() -> set[str]:
    return TEXT_EXTS | {".docx", ".xlsx", ".pptx", ".pdf"}


def _pypdf_reader(data: bytes):
    from pypdf import PdfReader

    return PdfReader(io.BytesIO(data))


class MinerUClient:
    """MinerU HTTP 服务（docker 部署，MINERU_BASE_URL 配置驱动；数据不出门）。"""

    def __init__(self, base_url: str, transport=None, timeout: float = 300):
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self._base = base_url.rstrip("/")

    def parse(self, filename: str, raw: bytes) -> str:
        resp = self._client.post(
            f"{self._base}/file_parse",
            files={"files": (filename, raw, "application/pdf")},
        )
        resp.raise_for_status()
        results = resp.json().get("results", {})
        for r in results.values():
            md = r.get("md_content") or r.get("md")
            if md:
                return md
        raise RuntimeError("MinerU 未返回解析内容")


def _parse_office_docx(raw: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(raw))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _parse_office_xlsx(raw: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"## 工作表：{ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _parse_office_pptx(raw: bytes) -> str:
    from pptx import Presentation

    p = Presentation(io.BytesIO(raw))
    parts = []
    for i, slide in enumerate(p.slides, 1):
        texts = [sh.text_frame.text for sh in slide.shapes
                 if sh.has_text_frame and sh.text_frame.text.strip()]
        if texts:
            parts.append(f"## 幻灯片{i}\n" + "\n".join(texts))
    return "\n\n".join(parts)


def _parse_pdf(raw: bytes, mineru: MinerUClient | None, filename: str) -> str:
    reader = _pypdf_reader(raw)
    texts = [(page.extract_text() or "").strip() for page in reader.pages]
    empty = sum(1 for t in texts if not t)
    if reader.pages and empty / len(reader.pages) >= SCANNED_PAGE_RATIO:
        if mineru is None:
            raise ScannedPdfError(
                f"{filename} 是扫描件（无文字层页 ≥{SCANNED_PAGE_RATIO:.0%}），"
                "需 MinerU OCR：请配置 MINERU_BASE_URL 并启动解析服务")
        return mineru.parse(filename, raw)
    return "\n\n".join(f"[第{i}页]\n{t}" for i, t in enumerate(texts, 1) if t)


def parse_document(filename: str, raw: bytes,
                   mineru: MinerUClient | None = None) -> str:
    ext = filename[filename.rfind("."):].lower() if "." in filename else ""
    if ext in TEXT_EXTS:
        return raw.decode("utf-8").strip()
    if ext == ".docx":
        return _parse_office_docx(raw)
    if ext == ".xlsx":
        return _parse_office_xlsx(raw)
    if ext == ".pptx":
        return _parse_office_pptx(raw)
    if ext == ".pdf":
        return _parse_pdf(raw, mineru, filename)
    raise ValueError(f"不支持的文件类型：{ext or filename}")
