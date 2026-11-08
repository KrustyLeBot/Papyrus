"""What the text says, without any generative model: distinctive keywords (TF-IDF against the archive), an
extractive summary (the most representative sentences, quoted as written), and a title taken from the text when the
file name says nothing (scan0012.pdf). Everything here is statistics on words."""
from __future__ import annotations

import math
import re
from collections import Counter

from .stopwords import STOP
from .text import fold

_WORD = re.compile(r"[A-Za-zÀ-ÿ]{4,25}")
MAX_KEYWORDS = 4
MIN_KEYWORD_SCORE = 1.2


def term_stats(text: str) -> tuple[Counter, dict[str, str]]:
    """Counts of folded words (stop words out) and the written form of each (first seen, lower case)."""
    counts: Counter = Counter()
    forms: dict[str, str] = {}
    for m in _WORD.finditer(text[:200_000]):
        original = m.group(0).lower()
        folded = fold(original)
        if folded in STOP or len(folded) < 4:
            continue
        counts[folded] += 1
        forms.setdefault(folded, original)
    return counts, forms


def term_scores(counts: Counter, doc_freq: dict[str, int], n_docs: int) -> dict[str, float]:
    """TF-IDF: a word scores high when it comes back in this document and is rare in the others."""
    # +0.5: in a nearly empty archive nothing is "rare" yet, but a word said twice still is the document's subject
    return {t: (1 + math.log(tf)) * (math.log((n_docs + 2) / (doc_freq.get(t, 0) + 1)) + 0.5) for t, tf in counts.items()}


def keywords(text: str, title: str, counts: Counter, forms: dict[str, str], scores: dict[str, float]) -> list[str]:
    head = fold(title + "\n" + text[:300])
    picked = []
    for term, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        if score < MIN_KEYWORD_SCORE:
            break
        if counts[term] >= 2 or term in head:
            picked.append(forms[term])
        if len(picked) >= MAX_KEYWORDS:
            break
    return picked


# ---------------------------------------------------------------------------------------------- summary

def sentences(text: str) -> list[str]:
    out = []
    for paragraph in re.split(r"\n\s*\n|\f", text):
        joined = re.sub(r"\s*\n\s*", " ", paragraph).strip()
        for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý«\"(])", joined):
            s = re.sub(r"\s+", " ", s).strip(" -–•·")
            if s:
                out.append(s)
    return out


def _usable(s: str) -> bool:
    if not 40 <= len(s) <= 320:
        return False
    letters = sum(c.isalpha() for c in s)
    digits = sum(c.isdigit() for c in s)
    return letters / len(s) >= 0.6 and digits / len(s) < 0.1 and "@" not in s and "http" not in s.lower()


def extractive_summary(text: str, scores: dict[str, float], limit: int = 420) -> str:
    """The two or three most representative sentences, in reading order, as the document wrote them."""
    sents = sentences(text[:60_000])
    scored = []
    for i, s in enumerate(sents):
        if not _usable(s):
            continue
        words = {fold(w.lower()) for w in _WORD.findall(s)}
        weight = sum(scores.get(w, 0) for w in words) / math.sqrt(max(len(words), 1))
        scored.append((weight + 0.4 / (1 + i * 0.2), i, s))
    if not scored:
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if len(ln) >= 12 and sum(c.isalpha() for c in ln) / len(ln) >= 0.6][:2]
        return _cut(" — ".join(lines), limit)
    best = sorted(sorted(scored, reverse=True)[:3], key=lambda t: t[1])
    return _cut(" ".join(s for _w, _i, s in best), limit)


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


# ---------------------------------------------------------------------------------------------- title

_JUNK = re.compile(r"^(scan(ner)?|img|dsc|dscn|image|photo|doc|document|documents|screenshot|capture|cam|numeris\w*|file|"
                   r"fichier|pdf|untitled|sans titre|new|nouveau|page|pages|copie|copy|download|telechargement|"
                   r"print|impression|output|export|attachment|piece jointe|pj)[\s_\-.()\d]*$")
_TITLE_WORDS = re.compile(r"contrat|facture|attestation|diplome|releve|bulletin|certificat|avis |decompte|quittance|"
                          r"ordonnance|convention|devis|offre|acte |compromis|notice|manuel|lettre|relance|"
                          r"confirmation|rapport|compte rendu|resultat|declaration|fiche|bail|police |echeancier|"
                          r"demande|autorisation|licence|permis|passeport|carte |reglement|garantie|inscription")
_GENERIC = re.compile(r"republique|ministere|academie|page \d|prefecture|royaume|confederation|www\.|@|\btel\b|\bfax\b")


def is_junk_name(stem: str) -> bool:
    """A file name that says nothing about the document."""
    folded = fold(stem).strip()
    letters = re.sub(r"[^a-z]", "", folded)
    return (len(letters) < 3 or bool(_JUNK.match(folded)) or bool(re.fullmatch(r"[0-9a-f\-_ ]{16,}", folded))
            or bool(re.fullmatch(r"(?:img|dsc|pxl|mvimg|photo)?[\d_\- ]{6,}", folded)))


def derive_title(text: str) -> str | None:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln][:25]
    best, best_score = None, 1.9
    for i, line in enumerate(lines):
        if not 6 <= len(line) <= 90 or len(line.split()) < 2:
            continue
        letters = sum(c.isalpha() for c in line)
        if letters / len(line) < 0.65 or sum(c.isdigit() for c in line) > 8:
            continue
        folded = fold(line)
        score = 3 - i * 0.1
        if _TITLE_WORDS.search(folded):
            score += 3
        if line.isupper():
            score += 0.7
        if _GENERIC.search(folded):
            score -= 3
        if score > best_score:
            best, best_score = line, score
    if best is None:
        return None
    title = best.strip(" .:;,-–|")
    if title.isupper():
        title = title.capitalize()
    return title[:90] or None
