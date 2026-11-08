"""Search (§10): FTS5 with BM25 (accents ignored, prefixes, phrases, every word required) and a semantic ranking
(cosine over every document's embedding), merged by Reciprocal Rank Fusion."""
from __future__ import annotations

import html
import json
import re
import threading
import time

import numpy as np

from .db import Database, blob_to_vec
from .embed import Embedder
from .text import fold

RRF_K = 60
SEMANTIC_TOP = 50
# multilingual-e5-small: a related document scores 0.86-0.89 against a query, an unrelated one 0.77-0.84 (measured on
# payslips, contracts, a diploma, invoices, a manual): below this, a result is noise, not a match
SEMANTIC_FLOOR = 0.86  # the default of the adjustable "similarité minimale"
_MARK_OPEN, _MARK_CLOSE = "\x02", "\x03"


def edit_distance(a: str, b: str, limit: int) -> int:
    """Damerau-Levenshtein distance, given up (limit + 1) as soon as it cannot stay within `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def tokenize(query: str) -> list[tuple[str, list[str], bool]]:
    """("phrase", words, False) for "quoted text", ("term", [word], explicit_prefix) for the others."""
    items = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', query):
        if m.group(1):
            words = re.findall(r"\w+", m.group(1))
            if words:
                items.append(("phrase", words, False))
            continue
        token = m.group(2)
        prefix = token.endswith("*")
        for word in re.findall(r"\w+", token):
            items.append(("term", [word], prefix))
    return items


def fts_query(query: str, prefix_last: bool = False, expand=None) -> str | None:
    """User syntax → FTS5: "exact phrase", prefix*, every other word quoted (so FTS operators typed by mistake are
    plain words); every term must match. `prefix_last` completes the last word as it is typed (« salai » finds
    salaire); `expand(word)` may return spelling alternatives for a word the archive does not know."""
    items = tokenize(query)
    if not items:
        return None
    last_term = max((i for i, it in enumerate(items) if it[0] == "term"), default=-1)
    parts = []
    for i, (kind, words, explicit) in enumerate(items):
        if kind == "phrase":
            parts.append('"' + " ".join(words) + '"')
            continue
        word = words[0]
        completing = explicit or (prefix_last and i == last_term)
        alternatives = expand(word, completing) if expand and not explicit else [word]
        head = f'"{word}"*' if completing else f'"{word}"'
        others = [f'"{a}"' for a in alternatives if a != word]
        parts.append("(" + " OR ".join([head, *others]) + ")" if others else head)
    return " AND ".join(parts)


def _snippet_html(raw: str) -> str:
    return html.escape(raw).replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>")


class VectorIndex:
    """Every document's embedding in one matrix, loaded on the first semantic search and dropped when idle."""

    def __init__(self, db: Database, idle_seconds: int = 900, dim: int = 384):
        self.db = db
        self.idle_seconds = idle_seconds
        self.dim = dim
        self._lock = threading.Lock()
        self._ids: np.ndarray | None = None
        self._matrix: np.ndarray | None = None
        self._stamp: tuple | None = None
        self._used = 0.0

    def _current_stamp(self) -> tuple:
        row = self.db.conn.execute("SELECT COUNT(*), MAX(updated_at), SUM(LENGTH(embedding)) FROM documents "
                                   "WHERE embedding IS NOT NULL AND status = 'done' AND hidden = 0").fetchone()
        return tuple(row)

    def get(self) -> tuple[np.ndarray, np.ndarray]:
        stamp = self._current_stamp()
        with self._lock:
            if self._matrix is None or stamp != self._stamp:
                rows = self.db.conn.execute("SELECT id, embedding FROM documents WHERE embedding IS NOT NULL "
                                            "AND status = 'done' AND hidden = 0").fetchall()
                rows = [r for r in rows if len(r["embedding"]) == self.dim * 4]  # another model's vectors are skipped
                self._ids = np.array([r["id"] for r in rows], dtype=np.int64)
                self._matrix = (np.vstack([blob_to_vec(r["embedding"]) for r in rows]) if rows
                                else np.zeros((0, self.dim), dtype=np.float32))
                self._stamp = stamp
            self._used = time.monotonic()
            return self._ids, self._matrix

    def drop_if_idle(self) -> None:
        if not self.idle_seconds:  # EMBED_IDLE_UNLOAD_MIN=0: keep it loaded
            return
        with self._lock:
            if self._matrix is not None and time.monotonic() - self._used > self.idle_seconds:
                self._ids = self._matrix = None


class Searcher:
    def __init__(self, db: Database, embedder: Embedder, vectors: VectorIndex, defaults: dict | None = None):
        self.db = db
        self.embedder = embedder
        self.vectors = vectors
        self.defaults = {"semantic_min": SEMANTIC_FLOOR, "prefix": True, "fuzzy": True, **(defaults or {})}

    # ---------------------------------------------------------------- adjustable settings (Administration)

    def settings(self) -> dict:
        out = dict(self.defaults)
        raw = self.db.setting("search")
        if raw:
            try:
                saved = json.loads(raw)
                out["semantic_min"] = min(0.95, max(0.5, float(saved.get("semantic_min", out["semantic_min"]))))
                out["prefix"] = bool(saved.get("prefix", out["prefix"]))
                out["fuzzy"] = bool(saved.get("fuzzy", out["fuzzy"]))
            except (ValueError, TypeError, AttributeError):
                pass
        return out

    def save_settings(self, semantic_min: float, prefix: bool, fuzzy: bool) -> dict:
        with self.db.tx():
            self.db.set_setting("search", json.dumps({"semantic_min": round(float(semantic_min), 3), "prefix": bool(prefix),
                                                      "fuzzy": bool(fuzzy)}))
        return self.settings()

    # ---------------------------------------------------------------- spelling

    def _known(self, word: str) -> bool:
        return self.db.conn.execute("SELECT 1 FROM docs_vocab WHERE term = ? LIMIT 1", (word,)).fetchone() is not None

    def _starts(self, word: str) -> bool:
        if not word.isalnum():
            return True
        return self.db.conn.execute("SELECT 1 FROM docs_vocab WHERE term GLOB ? LIMIT 1", (word + "*",)).fetchone() is not None

    def _near(self, word: str) -> list[str]:
        """Words of the index within one typo (two for long words) of `word`, same first letter."""
        folded = fold(word)
        if len(folded) < 5 or not folded.isalnum():
            return []
        limit = 1 if len(folded) < 8 else 2
        rows = self.db.conn.execute(
            "SELECT DISTINCT term FROM docs_vocab WHERE term GLOB ? AND length(term) BETWEEN ? AND ?",
            (folded[0] + "*", len(folded) - limit, len(folded) + limit)).fetchall()
        scored = sorted((edit_distance(folded, r["term"], limit), r["term"]) for r in rows)
        return [t for d, t in scored if d <= limit and t != folded][:6]

    # ---------------------------------------------------------------- filters

    def _filter_ids(self, folder: str | None, children: bool, tag: str | None, type_name: str | None,
                    date_from: str | None, date_to: str | None) -> set[int] | None:
        clauses, params = [], []
        if folder == ".":
            clauses.append("id IN (SELECT doc_id FROM locations WHERE folder = '')")
        elif folder:
            like = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
            if children:
                clauses.append("id IN (SELECT doc_id FROM locations WHERE folder = ? OR folder LIKE ? ESCAPE '\\')")
                params += [folder, like]
            else:
                clauses.append("id IN (SELECT doc_id FROM locations WHERE folder = ?)")
                params.append(folder)
        if tag:
            clauses.append("id IN (SELECT dt.doc_id FROM doc_tags dt JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)")
            params.append(tag)
        if type_name:
            clauses.append("EXISTS (SELECT 1 FROM json_each(documents.types) WHERE value = ?)")
            params.append(type_name)
        if date_from:
            clauses.append("doc_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("doc_date <= ?")
            params.append(date_to)
        if not clauses:
            return None
        rows = self.db.conn.execute(f"SELECT id FROM documents WHERE status = 'done' AND {' AND '.join(clauses)}", params)
        return {r["id"] for r in rows}

    def search(self, query: str, exact: bool = False, folder: str | None = None, children: bool = True,
               tag: str | None = None, type_name: str | None = None, date_from: str | None = None,
               date_to: str | None = None, limit: int = 50) -> dict:
        started = time.perf_counter()
        cfg = self.settings()
        allowed = self._filter_ids(folder, children, tag, type_name, date_from, date_to)
        conn = self.db.conn
        text_rank: list[int] = []
        snippets: dict[int, str] = {}
        corrections: dict[str, list[str]] = {}

        def expand(word: str, completing: bool = False) -> list[str]:
            folded = fold(word)
            if not cfg["fuzzy"] or (self._starts(folded) if completing else self._known(folded)):
                return [word]
            near = self._near(word)
            if near:
                corrections[word] = near
            return [word, *near]

        match = fts_query(query, prefix_last=cfg["prefix"] and not exact, expand=None if exact else expand)
        if match:
            rows = conn.execute(
                "SELECT rowid AS id, snippet(docs_fts, 1, ?, ?, '…', 18) AS snip, "
                "snippet(docs_fts, 0, ?, ?, '…', 12) AS tsnip FROM docs_fts WHERE docs_fts MATCH ? "
                "ORDER BY bm25(docs_fts, 10.0, 1.0, 5.0, 3.0, 2.0) LIMIT 500",
                (_MARK_OPEN, _MARK_CLOSE, _MARK_OPEN, _MARK_CLOSE, match)).fetchall()
            for r in rows:
                if allowed is not None and r["id"] not in allowed:
                    continue
                text_rank.append(r["id"])
                snip = r["snip"] if _MARK_OPEN in (r["snip"] or "") else r["tsnip"]
                snippets[r["id"]] = _snippet_html(snip or "")

        sem_rank: list[int] = []
        sem_scores: dict[int, float] = {}
        semantic_used = False
        if not exact and query.strip() and self.embedder.available:
            try:
                qvec = self.embedder.embed_query(query)
                ids, matrix = self.vectors.get()
                if len(ids):
                    sims = matrix @ qvec
                    order = np.argsort(-sims)
                    for i in order:
                        if len(sem_rank) >= SEMANTIC_TOP or sims[i] < cfg["semantic_min"]:
                            break
                        doc_id = int(ids[i])
                        if allowed is not None and doc_id not in allowed:
                            continue
                        sem_rank.append(doc_id)
                        sem_scores[doc_id] = float(sims[i])
                semantic_used = True
            except Exception as error:  # noqa: BLE001 - fall back to text only
                print(f"papyrus: semantic search unavailable ({error})", flush=True)

        fused: dict[int, float] = {}
        how: dict[int, set[str]] = {}
        for rank, doc_id in enumerate(text_rank):
            fused[doc_id] = fused.get(doc_id, 0) + 1 / (RRF_K + rank + 1)
            how.setdefault(doc_id, set()).add("texte")
        for rank, doc_id in enumerate(sem_rank):
            fused[doc_id] = fused.get(doc_id, 0) + 1 / (RRF_K + rank + 1)
            how.setdefault(doc_id, set()).add("sens")
        ordered = sorted(fused, key=lambda d: -fused[d])
        total = len(ordered)
        ordered = ordered[:limit]
        return {
            "ids": ordered,
            "total": total,
            "snippets": snippets,
            "how": {d: " + ".join(sorted(how[d], key=lambda s: s != "texte")) for d in ordered},
            "similarity": sem_scores,
            "semantic": semantic_used,
            "corrections": corrections,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def similar(self, doc_id: int, limit: int = 10) -> list[tuple[int, float]]:
        row = self.db.conn.execute("SELECT embedding FROM documents WHERE id = ?", (doc_id,)).fetchone()
        vec = blob_to_vec(row["embedding"]) if row else None
        if vec is None or vec.shape[0] != self.vectors.dim:
            return []
        ids, matrix = self.vectors.get()
        if not len(ids):
            return []
        sims = matrix @ vec
        order = np.argsort(-sims)
        out = []
        for i in order:
            if int(ids[i]) == doc_id:
                continue
            out.append((int(ids[i]), float(sims[i])))
            if len(out) >= limit:
                break
        return out
