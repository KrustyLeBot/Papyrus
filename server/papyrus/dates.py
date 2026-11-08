"""The document's date, found in its text (French formats first, a little English).

Every date found is scored: a keyword just before it (période, fait le, émis le, échéance…) weighs most, an early
position a little; birth dates are pushed away. Dates before 1950 or more than a year ahead are dropped."""
from __future__ import annotations

import datetime as dt
import re

from .text import fold

MONTHS = {
    "janvier": 1, "janv": 1, "jan": 1, "january": 1,
    "fevrier": 2, "fevr": 2, "fev": 2, "feb": 2, "february": 2,
    "mars": 3, "mar": 3, "march": 3,
    "avril": 4, "avr": 4, "apr": 4, "april": 4,
    "mai": 5, "may": 5,
    "juin": 6, "jun": 6, "june": 6,
    "juillet": 7, "juil": 7, "jul": 7, "july": 7,
    "aout": 8, "aug": 8, "august": 8,
    "septembre": 9, "sept": 9, "sep": 9, "september": 9,
    "octobre": 10, "oct": 10, "october": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "decembre": 12, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

# all patterns run on folded text (lower case, no accents)
_NUMERIC = re.compile(r"(?<![\d/.\-])(\d{1,2})\s?([/.\-])\s?(\d{1,2})\2\s?(\d{4}|\d{2})(?![\d/.\-]\d)")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_LONG = re.compile(rf"(?<![a-z0-9])(1er|\d{{1,2}})\s+({_MONTH_RE})\.?\s+(\d{{4}})(?!\d)")
_MONTH_YEAR = re.compile(rf"(?<![a-z0-9])({_MONTH_RE})\.?\s+(\d{{4}})(?!\d)")
_EN_LONG = re.compile(rf"(?<![a-z0-9])({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?!\d)")

_KEYWORDS = [
    (re.compile(r"date de paiement|paye le|paiement le|verse le"), 5),
    (re.compile(r"periode|mois de|pour le mois|salaire de"), 4),
    # a letter's dateline: "Grenoble, le 13 juillet 2017" (a place, a comma, "le")
    (re.compile(r"(?:^|\n)\s*[a-z][a-z' -]{1,30},\s*le\s*$"), 4),
    (re.compile(r"fait (?:a [^\n,]{1,30},? )?le|emis le|emise le|etabli le|etablie le|signe le|en date du|date d'emission|date de facture|date du document|date facture|invoice date|dated?"), 4),
    (re.compile(r"echeance|date limite|a payer avant"), 3),
    (re.compile(r"\bdate\b|\ble\s*$"), 2),
]
_NEGATIVE = re.compile(r"a compter d[ue]|a partir d[ue]|prend effet|ne\(?e?\)? le|date de naissance|naissance|born|depuis le|entree le|date d'entree|anciennete|valable jusqu|expire|validite")


def _year(y: str) -> int:
    value = int(y)
    if len(y) == 2:
        value += 2000 if value <= 69 else 1900
    return value


def _valid(year: int, month: int, day: int, today: dt.date) -> dt.date | None:
    try:
        date = dt.date(year, month, day)
    except ValueError:
        return None
    if date.year < 1950 or date > today + dt.timedelta(days=366):
        return None
    return date


def candidates(text: str, today: dt.date | None = None) -> list[tuple[float, int, dt.date]]:
    """(score, position, date) for every plausible date of the text."""
    today = today or dt.date.today()
    folded = fold(text[:200_000])
    found: list[tuple[int, int, dt.date, bool]] = []  # position, end, date, has_day
    for m in _ISO.finditer(folded):
        d = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)), today)
        if d:
            found.append((m.start(), m.end(), d, True))
    for m in _NUMERIC.finditer(folded):
        d = _valid(_year(m.group(4)), int(m.group(3)), int(m.group(1)), today)
        if d:
            found.append((m.start(), m.end(), d, True))
    for m in _LONG.finditer(folded):
        day = 1 if m.group(1) == "1er" else int(m.group(1))
        d = _valid(int(m.group(3)), MONTHS[m.group(2)], day, today)
        if d:
            found.append((m.start(), m.end(), d, True))
    for m in _EN_LONG.finditer(folded):
        d = _valid(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)), today)
        if d:
            found.append((m.start(), m.end(), d, True))
    spans = [(s, e) for s, e, _, _ in found]
    for m in _MONTH_YEAR.finditer(folded):
        if any(s <= m.start() < e for s, e in spans):
            continue  # part of a full date already found
        d = _valid(int(m.group(2)), MONTHS[m.group(1)], 1, today)
        if d:
            found.append((m.start(), m.end(), d, False))

    out = []
    length = max(len(folded), 1)
    for start, _end, date, has_day in found:
        before = folded[max(0, start - 40):start]
        # the keyword must sit on the same line, close to the date
        line_before = before.rsplit("\n", 1)[-1]
        score = 1.0
        for pattern, weight in _KEYWORDS:
            if pattern.search(line_before):
                score += weight
                break
        if _NEGATIVE.search(line_before):
            score -= 6
        if start < 2000:
            score += 1.0 - start / 2000
        score += 0.3 * (1 - start / length)
        if not has_day:
            score -= 0.5
        if date > today:
            score -= 1.5  # due dates and validity limits more often than the document's own date
        out.append((score, start, date))
    return out


def detect(text: str, today: dt.date | None = None) -> dt.date | None:
    found = candidates(text, today)
    if not found:
        return None
    best = max(found, key=lambda c: (c[0], -c[1]))
    if best[0] <= 0:
        return None
    return best[2]
