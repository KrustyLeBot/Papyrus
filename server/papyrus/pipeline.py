"""Processing of one document (§5): text, date, title, types, keys, embedding, groups and tags, thumbnail, index.
Plus the background worker that runs it, one document at a time, from the queue kept in the database."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import traceback
from pathlib import Path

from . import dates, extract, identifiers, thumbs
from .config import Config
from .db import Database, blob_to_vec, vec_to_blob
from .embed import Embedder
from .grouping import Grouper, now
from .rules import RulesStore
from .text import fold


def index_document(db: Database, doc_id: int) -> None:
    """(Re)writes the document's row of the full-text index: title, text, file name, tags."""
    conn = db.conn
    row = conn.execute("SELECT title, text, original_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
    if row is None:
        return
    tags = " ".join(r["name"] for r in conn.execute(
        "SELECT t.name FROM doc_tags dt JOIN tags t ON t.id = dt.tag_id WHERE dt.doc_id = ?", (doc_id,)))
    conn.execute("INSERT INTO docs_fts(rowid, title, body, filename, tags) VALUES (?, ?, ?, ?, ?)",
                 (doc_id, row["title"], row["text"], row["original_name"], tags))


def tag_id(db: Database, name: str) -> int:
    conn = db.conn
    name = " ".join(name.split())[:60]
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO tags(name) VALUES (?)", (name,)).lastrowid


def add_tag(db: Database, doc_id: int, name: str, source: str) -> int:
    tid = tag_id(db, name)
    db.conn.execute(
        "INSERT INTO doc_tags(doc_id, tag_id, source) VALUES (?, ?, ?) ON CONFLICT(doc_id, tag_id) DO UPDATE SET "
        "source = CASE WHEN excluded.source = 'manual' THEN 'manual' ELSE doc_tags.source END", (doc_id, tid, source))
    return tid


def prune_tags(db: Database) -> None:
    db.conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM doc_tags)")


class Processor:
    def __init__(self, config: Config, db: Database, rules: RulesStore, embedder: Embedder, grouper: Grouper):
        self.config = config
        self.db = db
        self.rules = rules
        self.embedder = embedder
        self.grouper = grouper

    def path_of(self, row) -> Path:
        return self.config.data_dir / row["path"]

    # ---------------------------------------------------------------- steps that the reclassification reuses

    def retype(self, doc_id: int) -> list[str]:
        row = self.db.conn.execute("SELECT title, text, original_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
        matched = self.rules.rules.match(row["text"], row["title"] + " " + row["original_name"])
        names = [rule.name for rule, _score in matched]
        self.db.conn.execute("UPDATE documents SET types = ? WHERE id = ?", (json.dumps(names, ensure_ascii=False), doc_id))
        self.auto_tags(doc_id)
        return names

    def auto_tags(self, doc_id: int) -> None:
        """Year, types, the main entity's name and the rules' tags; tags added by hand are never removed."""
        conn = self.db.conn
        row = conn.execute("SELECT doc_date, types, title, text FROM documents WHERE id = ?", (doc_id,)).fetchone()
        conn.execute("DELETE FROM doc_tags WHERE doc_id = ? AND source = 'auto'", (doc_id,))
        names: list[str] = []
        if row["doc_date"]:
            names.append(row["doc_date"][:4])
        for type_name in json.loads(row["types"] or "[]"):
            rule = self.rules.rules.by_name(type_name)
            rule_tags = rule.tags if rule else []
            # the type's own name as a tag, unless the rule's tags already say it (Factures / facture)
            if not any(fold(t) and (fold(type_name).startswith(fold(t)) or fold(t).startswith(fold(type_name))) for t in rule_tags):
                names.append(type_name.lower())
            names.extend(rule_tags)
        names.extend(self.rules.rules.content_tags(row["text"], row["title"]))
        entity = conn.execute("SELECT label FROM doc_keys WHERE doc_id = ? AND key IN ('employer_name', 'company') "
                              "ORDER BY CASE key WHEN 'employer_name' THEN 0 ELSE 1 END, rowid LIMIT 1", (doc_id,)).fetchone()
        if entity and entity["label"]:
            names.append(entity["label"])
        seen = set()
        for name in names:
            if name and name.lower() not in seen:
                seen.add(name.lower())
                add_tag(self.db, doc_id, name, "auto")
        prune_tags(self.db)
        index_document(self.db, doc_id)

    def embed(self, doc_id: int) -> bool:
        if not self.embedder.available:
            return False
        row = self.db.conn.execute("SELECT title, text FROM documents WHERE id = ?", (doc_id,)).fetchone()
        try:
            vec = self.embedder.embed_document(row["title"], row["text"])
        except Exception as error:  # noqa: BLE001 - degraded mode rather than a failed document
            self.embedder.error = f"échec du calcul d'embedding : {error}"
            print(f"papyrus: {self.embedder.error}", flush=True)
            return False
        self.db.conn.execute("UPDATE documents SET embedding = ? WHERE id = ?", (vec_to_blob(vec), doc_id))
        return True

    # ---------------------------------------------------------------- the whole pipeline

    def process(self, doc_id: int, progress=None) -> None:
        conn = self.db.conn
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None or row["status"] == "password_required":
            return
        path = self.path_of(row)
        cfg = self.config
        result = extract.extract(path, row["ext"], cfg.ocr_langs, cfg.ocr_threads, cfg.ocr_max_pages, progress)
        text, _masked = identifiers.mask_sensitive(result.text)

        if progress:
            progress("Analyse")
        upload_day = dt.date.fromisoformat(row["created_at"][:10])
        if row["date_source"] == "manual" and row["doc_date"]:
            doc_date, source = row["doc_date"], "manual"
        else:
            found = dates.detect(text, today=max(dt.date.today(), upload_day))
            doc_date, source = (found.isoformat(), "detected") if found else (upload_day.isoformat(), "upload")

        with self.db.tx():
            conn.execute("UPDATE documents SET text = ?, pages = ?, ocr = ?, doc_date = ?, date_source = ?, updated_at = ? "
                         "WHERE id = ?", (text, result.pages, int(result.ocr), doc_date, source, now(), doc_id))
            conn.execute("DELETE FROM doc_keys WHERE doc_id = ?", (doc_id,))
            for key in identifiers.extract(text, int(doc_date[:4])):
                conn.execute("INSERT OR IGNORE INTO doc_keys(doc_id, key, value, label) VALUES (?, ?, ?, ?)",
                             (doc_id, key.key, key.value, key.label))
            self.retype(doc_id)

        if progress:
            progress("Calcul de l'empreinte sémantique")
        self.embed(doc_id)
        self.grouper.replace(doc_id)
        with self.db.tx():
            self.auto_tags(doc_id)

        if progress:
            progress("Vignette")
        thumbs.make(path, row["ext"], cfg.thumbs_dir / f"{row['uuid']}.webp", text)
        with self.db.tx():
            conn.execute("UPDATE documents SET status = 'done', error = NULL, processed_at = ?, updated_at = ? WHERE id = ?",
                         (now(), now(), doc_id))


class Worker:
    """One background thread: documents waiting in the queue first, then jobs (re-embedding, backups)."""

    def __init__(self, processor: Processor, config: Config, db: Database):
        self.processor = processor
        self.config = config
        self.db = db
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._jobs: list[tuple[str, object]] = []
        self._jobs_lock = threading.Lock()
        self.current: dict | None = None
        self.thread: threading.Thread | None = None
        self.embedded_version = -1
        self.last_backup_day: str | None = None

    def start(self) -> None:
        with self.db.tx():
            self.db.conn.execute("UPDATE documents SET status = 'pending' WHERE status = 'processing'")
        self.thread = threading.Thread(target=self._loop, name="papyrus-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def add_job(self, name: str, fn) -> None:
        with self._jobs_lock:
            if not any(n == name for n, _ in self._jobs):
                self._jobs.append((name, fn))
        self.wake()

    def jobs(self) -> list[str]:
        with self._jobs_lock:
            return [n for n, _ in self._jobs]

    def _progress(self, step: str) -> None:
        if self.current is not None:
            self.current["step"] = step

    def run_one(self) -> bool:
        """Processes the next waiting document; False when the queue is empty (tests call this directly)."""
        conn = self.db.conn
        row = conn.execute("SELECT id, title FROM documents WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return False
        doc_id = row["id"]
        with self.db.tx():
            conn.execute("UPDATE documents SET status = 'processing' WHERE id = ?", (doc_id,))
        self.current = {"id": doc_id, "title": row["title"], "step": "Extraction du texte", "started": time.time()}
        try:
            self.processor.process(doc_id, self._progress)
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the queue
            message = str(error) if isinstance(error, extract.ExtractError) else f"{error.__class__.__name__}: {error}"
            if not isinstance(error, extract.ExtractError):
                traceback.print_exc()
            with self.db.tx():
                conn.execute("UPDATE documents SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                             (message[:500], now(), doc_id))
        finally:
            self.current = None
        return True

    def _embed_missing(self) -> None:
        """When the model becomes available after documents were processed without it."""
        if not self.processor.embedder.available or self.embedded_version == self.processor.embedder.version:
            return
        self.embedded_version = self.processor.embedder.version
        missing = [r["id"] for r in self.db.conn.execute(
            "SELECT id FROM documents WHERE status = 'done' AND embedding IS NULL")]
        for doc_id in missing:
            if self._stop.is_set():
                return
            self.current = {"id": doc_id, "title": "", "step": "Calcul de l'empreinte sémantique", "started": time.time()}
            try:
                with self.db.tx():
                    self.processor.embed(doc_id)
                    self.processor.grouper.update_centroids(r["group_id"] for r in self.db.conn.execute(
                        "SELECT group_id FROM doc_groups WHERE doc_id = ?", (doc_id,)))
            finally:
                self.current = None

    def _backup(self) -> None:
        if not self.config.backup_keep:
            return
        today = dt.date.today().isoformat()
        if self.last_backup_day == today:
            return
        target = self.config.backups_dir / f"papyrus-{today}.db"
        if not target.exists():
            try:
                self.db.backup_to(target)
                print(f"papyrus: daily snapshot {target.name}", flush=True)
            except Exception as error:  # noqa: BLE001
                print(f"papyrus: daily snapshot failed ({error})", flush=True)
                return
        snapshots = sorted(self.config.backups_dir.glob("papyrus-????-??-??.db"))
        for old in snapshots[:-self.config.backup_keep]:
            old.unlink(missing_ok=True)
        self.last_backup_day = today

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.run_one():
                    continue
                with self._jobs_lock:
                    job = self._jobs.pop(0) if self._jobs else None
                if job:
                    name, fn = job
                    self.current = {"id": None, "title": name, "step": name, "started": time.time()}
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                    finally:
                        self.current = None
                    continue
                self._embed_missing()
                self._backup()
            except Exception:  # noqa: BLE001 - the worker must survive anything (a locked database…)
                traceback.print_exc()
                time.sleep(5)
            self._wake.wait(timeout=60)
            self._wake.clear()


def reembed_all(processor: Processor, db: Database) -> None:
    ids = [r["id"] for r in db.conn.execute("SELECT id FROM documents WHERE status = 'done'")]
    for doc_id in ids:
        with db.tx():
            processor.embed(doc_id)
    with db.tx():
        processor.grouper.update_centroids(r["id"] for r in db.conn.execute("SELECT id FROM groups"))


__all__ = ["Processor", "Worker", "index_document", "add_tag", "prune_tags", "reembed_all", "blob_to_vec"]
