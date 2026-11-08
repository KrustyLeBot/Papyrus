"""Search (§10): FTS5 with BM25 (accents ignored, prefixes, phrases, every word required) and a semantic ranking
(cosine over every document's embedding), merged by Reciprocal Rank Fusion."""
from __future__ import annotations

import html
import re
import threading
import time

import numpy as np

from .db import Database, blob_to_vec
from .embed import Embedder

RRF_K = 60
SEMANTIC_TOP = 50
SEMANTIC_FLOOR = 0.78  # below this, e5 similarities mean "unrelated"
_MARK_OPEN, _MARK_CLOSE = "\x02", "\x03"


def fts_query(query: str) -> str | None:
    """User syntax → FTS5: "exact phrase", prefix*, other words quoted (so FTS operators typed by mistake are
    plain words); every term must match."""
    parts = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', query):
        if m.group(1):
            words = re.findall(r"\w+", m.group(1))
            if words:
                parts.append('"' + " ".join(words) + '"')
            continue
        token = m.group(2)
        prefix = token.endswith("*")
        for word in re.findall(r"\w+", token):
            parts.append(f'"{word}"' + ("*" if prefix else ""))
    return " ".join(parts) if parts else None


def _snippet_html(raw: str) -> str:
    return html.escape(raw).replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>")


class VectorIndex:
    """Every document's embedding in one matrix, loaded on the first semantic search and dropped when idle."""

    def __init__(self, db: Database, idle_seconds: int = 900):
        self.db = db
        self.idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._ids: np.ndarray | None = None
        self._matrix: np.ndarray | None = None
        self._stamp: tuple | None = None
        self._used = 0.0

    def _current_stamp(self) -> tuple:
        row = self.db.conn.execute("SELECT COUNT(*), MAX(updated_at), SUM(LENGTH(embedding)) FROM documents "
                                   "WHERE embedding IS NOT NULL AND status = 'done'").fetchone()
        return tuple(row)

    def get(self) -> tuple[np.ndarray, np.ndarray]:
        stamp = self._current_stamp()
        with self._lock:
            if self._matrix is None or stamp != self._stamp:
                rows = self.db.conn.execute("SELECT id, embedding FROM documents WHERE embedding IS NOT NULL "
                                            "AND status = 'done'").fetchall()
                self._ids = np.array([r["id"] for r in rows], dtype=np.int64)
                self._matrix = (np.vstack([blob_to_vec(r["embedding"]) for r in rows]) if rows
                                else np.zeros((0, 384), dtype=np.float32))
                self._stamp = stamp
            self._used = time.monotonic()
            return self._ids, self._matrix

    def drop_if_idle(self) -> None:
        with self._lock:
            if self._matrix is not None and time.monotonic() - self._used > self.idle_seconds:
                self._ids = self._matrix = None


class Searcher:
    def __init__(self, db: Database, embedder: Embedder, vectors: VectorIndex):
        self.db = db
        self.embedder = embedder
        self.vectors = vectors

    def _filter_ids(self, group_ids: list[int] | None, tag: str | None, type_name: str | None,
                    date_from: str | None, date_to: str | None) -> set[int] | None:
        clauses, params = ["status = 'done'"], []
        if group_ids is not None:
            marks = ",".join("?" * len(group_ids)) or "NULL"
            clauses.append(f"id IN (SELECT doc_id FROM doc_groups WHERE group_id IN ({marks}))")
            params += group_ids
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
        if len(clauses) == 1:
            return None
        rows = self.db.conn.execute(f"SELECT id FROM documents WHERE {' AND '.join(clauses)}", params)
        return {r["id"] for r in rows}

    def search(self, query: str, exact: bool = False, group_ids: list[int] | None = None, tag: str | None = None,
               type_name: str | None = None, date_from: str | None = None, date_to: str | None = None,
               limit: int = 50) -> dict:
        started = time.perf_counter()
        allowed = self._filter_ids(group_ids, tag, type_name, date_from, date_to)
        conn = self.db.conn
        text_rank: list[int] = []
        snippets: dict[int, str] = {}
        match = fts_query(query)
        if match:
            rows = conn.execute(
                "SELECT rowid AS id, snippet(docs_fts, 1, ?, ?, '…', 18) AS snip, "
                "snippet(docs_fts, 0, ?, ?, '…', 12) AS tsnip FROM docs_fts WHERE docs_fts MATCH ? "
                "ORDER BY bm25(docs_fts, 10.0, 1.0, 5.0, 3.0) LIMIT 500",
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
                        if len(sem_rank) >= SEMANTIC_TOP or sims[i] < SEMANTIC_FLOOR:
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
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def similar(self, doc_id: int, limit: int = 10) -> list[tuple[int, float]]:
        row = self.db.conn.execute("SELECT embedding FROM documents WHERE id = ?", (doc_id,)).fetchone()
        vec = blob_to_vec(row["embedding"]) if row else None
        if vec is None:
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
