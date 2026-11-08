"""Thumbnails: the first page (or the image) as a WebP 480 px wide; text documents get a drawn page."""
from __future__ import annotations

import subprocess
import tempfile
import textwrap
from pathlib import Path

from .extract import IMAGE_EXTS, to_png

WIDTH = 480


def _save(img, target: Path) -> None:
    from PIL import Image

    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.convert("RGBA").split()[-1])
        img = background
    if img.width > WIDTH:
        img = img.resize((WIDTH, max(1, round(img.height * WIDTH / img.width))), Image.LANCZOS)
    tmp = target.with_name(target.name + ".part")
    img.save(tmp, "WEBP", quality=80, method=4)
    tmp.replace(target)


def _text_page(text: str, target: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (WIDTH, round(WIDTH * 1.414)), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    y = 36
    for paragraph in text.splitlines()[:60]:
        for line in textwrap.wrap(paragraph, 52) or [""]:
            draw.text((32, y), line, fill="#2A2440", font=font)
            y += 21
            if y > img.height - 36:
                break
        if y > img.height - 36:
            break
    _save(img, target)


def make(source: Path, ext: str, target: Path, text: str = "") -> bool:
    from PIL import Image

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="papyrus-thumb-") as tmp:
            if ext == "pdf":
                prefix = Path(tmp) / "t"
                subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-scale-to", str(WIDTH * 2), "-png", "-singlefile",
                                str(source), str(prefix)], check=True, capture_output=True, timeout=120)
                with Image.open(prefix.with_suffix(".png")) as img:
                    _save(img.convert("RGB"), target)
            elif ext in IMAGE_EXTS:
                png = Path(tmp) / "t.png"
                to_png(source, png, max_side=WIDTH * 2)
                with Image.open(png) as img:
                    _save(img, target)
            else:
                _text_page(text, target)
        return True
    except Exception as error:  # noqa: BLE001 - a missing thumbnail is not worth failing the document
        print(f"papyrus: no thumbnail for {source.name} ({error.__class__.__name__})", flush=True)
        return False
