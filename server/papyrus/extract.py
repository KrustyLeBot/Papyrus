"""Text extraction: pdftotext for PDFs with a text layer, Tesseract for scans and images, the XML of DOCX files,
and plain text files. Everything runs as subprocesses, one document at a time."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "tif", "tiff", "heic", "heif", "bmp", "gif"}
TEXT_EXTS = {"txt", "md", "markdown", "csv"}
SUPPORTED_EXTS = {"pdf", "docx"} | IMAGE_EXTS | TEXT_EXTS
MIME = {
    "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "tif": "image/tiff", "tiff": "image/tiff", "heic": "image/heic", "heif": "image/heif", "bmp": "image/bmp",
    "gif": "image/gif", "txt": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8", "csv": "text/csv; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MIN_CHARS_PER_PAGE = 50


class ExtractError(Exception):
    pass


@dataclass
class Extracted:
    text: str
    pages: int
    ocr: bool


def sniff_ext(head: bytes, filename: str) -> str | None:
    """The file's real kind from its first bytes (the name's extension only breaks ties between text formats)."""
    name_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if head.startswith(b"%PDF-") or b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1", b"ftypheif", b"ftyphevc"):
        return "heic"
    if head.startswith(b"BM"):
        return "bmp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"PK\x03\x04"):
        return "docx" if name_ext == "docx" else None
    if name_ext in TEXT_EXTS and b"\x00" not in head[:4096]:
        return name_ext
    return None


def _run(cmd: list[str], env: dict | None = None, timeout: int = 600) -> bytes:
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, check=False)
    except FileNotFoundError:
        raise ExtractError(f"outil absent : {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise ExtractError(f"{cmd[0]} : délai dépassé") from None
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ExtractError(f"{cmd[0]} : {message[-1] if message else 'échec'}")
    return done.stdout


def pdf_pages(path: Path) -> int:
    out = _run(["pdfinfo", str(path)]).decode("utf-8", "replace")
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def detect_rotation(image: Path, threads: int) -> int:
    """Degrees (0, 90, 180, 270) the page must be turned clockwise to stand upright, from Tesseract's orientation
    detection (osd). 0 when it cannot tell (too little text) or is not confident."""
    env = dict(os.environ, OMP_THREAD_LIMIT=str(threads))
    try:
        out = _run(["tesseract", str(image), "stdout", "--psm", "0", "-l", "osd"], env=env, timeout=120).decode("utf-8", "replace")
    except ExtractError:
        return 0
    angle = re.search(r"Rotate:\s*(\d+)", out)
    confidence = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not angle or not confidence or float(confidence.group(1)) < 1.5:
        return 0
    return int(angle.group(1)) % 360


def ocr_image(image: Path, langs: str, threads: int) -> str:
    """OCR of one page image; a page scanned sideways or upside down is turned upright first."""
    env = dict(os.environ, OMP_THREAD_LIMIT=str(threads))
    angle = detect_rotation(image, threads)
    if angle:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 200_000_000  # our own rendering, capped above: no "decompression bomb" warning
        with Image.open(image) as img:
            img.rotate(-angle, expand=True, fillcolor=255 if img.mode == "L" else (255, 255, 255)).save(image)
    return _run(["tesseract", str(image), "stdout", "-l", langs, "--psm", "3"], env=env).decode("utf-8", "replace")


MAX_OCR_PIXELS = 60_000_000  # a plan or a poster at 300 dpi is 100+ megapixels: Tesseract would take forever
MIN_OCR_DPI = 100


def page_dpi(path: Path, page: int, wanted: int = 300) -> int:
    """300 dpi, lowered for a very large page so the rendered image stays under MAX_OCR_PIXELS."""
    try:
        out = _run(["pdfinfo", "-f", str(page), "-l", str(page), str(path)]).decode("utf-8", "replace")
        m = re.search(r"Page\s+%d\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts" % page, out)
        if not m:
            return wanted
        width, height = float(m.group(1)), float(m.group(2))
    except ExtractError:
        return wanted
    if width <= 0 or height <= 0:
        return wanted
    fits = int(72 * (MAX_OCR_PIXELS / (width * height)) ** 0.5)
    return max(MIN_OCR_DPI, min(wanted, fits))


def _ocr_pdf_page(path: Path, page: int, langs: str, threads: int, workdir: Path) -> str:
    prefix = workdir / f"p{page}"
    _run(["pdftoppm", "-r", str(page_dpi(path, page)), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile",
          str(path), str(prefix)])
    image = prefix.with_suffix(".png")
    try:
        return ocr_image(image, langs, threads)
    finally:
        image.unlink(missing_ok=True)


def extract_pdf(path: Path, langs: str, threads: int, max_pages: int, progress=None) -> Extracted:
    pages = pdf_pages(path)
    text = _run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"]).decode("utf-8", "replace")
    per_page = text.split("\f")
    if per_page and not per_page[-1].strip():
        per_page = per_page[:-1]
    pages = pages or len(per_page)
    while len(per_page) < pages:
        per_page.append("")
    poor = [i for i, t in enumerate(per_page) if len(re.sub(r"\s", "", t)) < MIN_CHARS_PER_PAGE]
    if not poor:
        return Extracted("\n\f".join(per_page).strip(), pages, False)
    if max_pages:
        poor = poor[:max_pages]
    with tempfile.TemporaryDirectory(prefix="papyrus-ocr-") as tmp:
        for n, i in enumerate(poor, 1):
            if progress:
                progress(f"OCR, page {n} / {len(poor)}")
            ocr_text = _ocr_pdf_page(path, i + 1, langs, threads, Path(tmp))
            if len(ocr_text.strip()) > len(per_page[i].strip()):
                per_page[i] = ocr_text
    return Extracted("\n\f".join(p.strip() for p in per_page).strip(), pages, True)


def to_png(path: Path, target: Path, max_side: int | None = None) -> None:
    """Any image Pillow reads (HEIC too when pillow-heif is installed), turned upright, written as PNG."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 200_000_000  # a 100-megapixel scan is the user's own; beyond twice that is refused
    try:
        import pillow_heif  # noqa: F401

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    with Image.open(path) as img:
        img.seek(0)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        if max_side:
            img.thumbnail((max_side, max_side))
        img.save(target, "PNG")


def extract_image(path: Path, langs: str, threads: int) -> Extracted:
    with tempfile.TemporaryDirectory(prefix="papyrus-ocr-") as tmp:
        png = Path(tmp) / "page.png"
        try:
            to_png(path, png)
        except Exception as error:  # noqa: BLE001 - any decoder failure means "unreadable image"
            raise ExtractError(f"image illisible ({error.__class__.__name__})") from None
        return Extracted(ocr_image(png, langs, threads).strip(), 1, True)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(path: Path) -> Extracted:
    try:
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > 100 * 1024 * 1024:
                raise ExtractError("DOCX trop volumineux")
            xml = zf.read(info)
    except (zipfile.BadZipFile, KeyError):
        raise ExtractError("DOCX illisible (chiffré ou endommagé)") from None
    root = ElementTree.fromstring(xml)
    paragraphs = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    text = "\n".join(paragraphs).strip()
    return Extracted(text, max(1, len(text) // 3000 + 1), False)


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def extract_text_file(path: Path) -> Extracted:
    text = decode_text(path.read_bytes()[:20 * 1024 * 1024]).replace("\r\n", "\n").strip()
    return Extracted(text, max(1, len(text) // 3000 + 1), False)


def extract(path: Path, ext: str, langs: str = "fra+eng", threads: int = 2, max_pages: int = 0, progress=None) -> Extracted:
    if ext == "pdf":
        return extract_pdf(path, langs, threads, max_pages, progress)
    if ext in IMAGE_EXTS:
        if progress:
            progress("OCR, page 1 / 1")
        return extract_image(path, langs, threads)
    if ext == "docx":
        return extract_docx(path)
    if ext in TEXT_EXTS:
        return extract_text_file(path)
    raise ExtractError(f"format non pris en charge : {ext}")
