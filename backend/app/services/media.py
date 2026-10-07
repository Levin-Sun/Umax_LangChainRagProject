# 文档内嵌图片抽取（§A「图表入库」第一道工序）：把文档里的图表/照片抠出来，交给视觉模型转文字描述。
# 覆盖：docx/pptx/xlsx（zip 内的 media 目录）与 pdf（图片 XObject，经 pypdf + Pillow 解码）。
# 过滤：小图（图标/项目符号/logo）不进索引——否则每个项目符号都会变成一条"图片描述"噪声。
import io
import zipfile
from dataclasses import dataclass

MIN_BYTES = 4_096      # 小于此字节数的一律当图标丢弃
MIN_SIDE = 200         # 最短边像素门槛：图表/照片通常远大于此，图标远小于
MAX_IMAGES = 20        # 单篇文档最多处理多少张图（成本闸门）
OFFICE_MEDIA = {".docx": "word/media/", ".pptx": "ppt/media/", ".xlsx": "xl/media/"}

MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
               "tiff": "image/tiff", "emf": "image/emf", "wmf": "image/wmf"}


@dataclass
class ImageBlob:
    origin: str          # 来源标识（如 word/media/image1.png / pdf p1 Im0），排查用
    ext: str
    data: bytes
    width: int
    height: int

    def data_url(self) -> str:
        mime = MIME_BY_EXT.get(self.ext, "image/png")
        import base64

        return f"data:{mime};base64,{base64.b64encode(self.data).decode()}"


def _dims(data: bytes) -> tuple[int, int] | None:
    """用 Pillow 读尺寸（成熟件，覆盖 PNG/JPEG/GIF/BMP/TIFF…）；读不出就当未知。"""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return None


def _keep(data: bytes) -> tuple[int, int] | None:
    """过滤闸：太小（字节/像素）的直接丢。返回尺寸表示保留，None 表示丢弃。"""
    if len(data) < MIN_BYTES:
        return None
    size = _dims(data)
    if size is None:
        return None                      # 连尺寸都读不出（EMF/WMF 等矢量）不做视觉描述
    if min(size) < MIN_SIDE:
        return None
    return size


def _extract_office(ext: str, raw: bytes) -> list[ImageBlob]:
    prefix = OFFICE_MEDIA[ext]
    out: list[ImageBlob] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = sorted(n for n in z.namelist()
                           if n.startswith(prefix) and not n.endswith("/"))
            for name in names:
                if len(out) >= MAX_IMAGES:
                    break
                data = z.read(name)
                size = _keep(data)
                if size is None:
                    continue
                out.append(ImageBlob(origin=name, ext=name.rsplit(".", 1)[-1].lower(),
                                     data=data, width=size[0], height=size[1]))
    except Exception:
        return []                        # 坏文件/非 zip：抽取失败不影响正文解析
    return out


def _extract_pdf(raw: bytes) -> list[ImageBlob]:
    out: list[ImageBlob] = []
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        for pno, page in enumerate(reader.pages, 1):
            if len(out) >= MAX_IMAGES:
                break
            try:
                images = list(page.images)
            except Exception:
                continue                 # 单页抽图失败不影响其他页
            for img in images:
                if len(out) >= MAX_IMAGES:
                    break
                data = img.data
                size = _keep(data)
                if size is None:
                    continue
                ext = (img.name or "").rsplit(".", 1)[-1].lower() or "png"
                out.append(ImageBlob(origin=f"pdf p{pno} {img.name}", ext=ext,
                                     data=data, width=size[0], height=size[1]))
    except Exception:
        return []
    return out


def extract_images(filename: str, raw: bytes) -> list[ImageBlob]:
    ext = filename[filename.rfind("."):].lower() if "." in filename else ""
    if ext in OFFICE_MEDIA:
        return _extract_office(ext, raw)
    if ext == ".pdf":
        return _extract_pdf(raw)
    return []
