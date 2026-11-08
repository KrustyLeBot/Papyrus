"""Text helpers shared by the extractors and the rules: accent- and case-insensitive folding."""
from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"[ \t   ]+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "«": '"', "»": '"', "–": "-", "—": "-"})


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def fold(text: str) -> str:
    """Lower case, no accents, typographic quotes made plain, runs of spaces collapsed (newlines kept)."""
    text = strip_accents(text.translate(_QUOTES)).lower()
    return _SPACES.sub(" ", text)


def fold_index(text: str) -> tuple[str, list[int]]:
    """fold() that keeps, for each folded character, its index in the original text (for snippets and labels).
    Only used on short windows, so the per-character loop is fine."""
    out: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, ch in enumerate(text.translate(_QUOTES)):
        if ch in " \t   ":
            if prev_space:
                continue
            prev_space = True
            out.append(" ")
            index.append(i)
            continue
        prev_space = False
        for c in strip_accents(ch).lower():
            out.append(c)
            index.append(i)
    return "".join(out), index


def clean_title(filename: str) -> str:
    """A readable title from a file name: no extension, separators made spaces, first letter capitalised."""
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = re.sub(r"[_\-.+]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    # scanner and camera names carry no meaning: keep them, but readable
    if not stem:
        return "Document"
    return stem[0].upper() + stem[1:]


def one_line(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
