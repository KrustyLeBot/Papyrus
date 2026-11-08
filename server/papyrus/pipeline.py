"""Analysis of one document (§5): text, date, title, types, identifiers, embedding, tags, summary, thumbnail, index.
Plus the background worker that runs it, one document at a time, from the queue kept in the database."""
from __future__ import annotations

import datetime as dt
import json
import posixpath
import threading
import time
import traceback
from pathlib import Path

from . import dates, extract, identifiers, pdfcrypt, summary as summarizer, thumbs
from .config import Config
from .db import Database, vec_to_blob
from .embed import Embedder, _trim_memory as trim_memory
from .rules import RulesStore
from .text import fold


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def index_document(db: Database, doc_id: int) -> None:
    """(Re)writes the document's row of the full-text index: title, text, file name, tags, folders."""
    conn = db.conn
    row = conn.execute("SELECT title, text, original_name, hidden FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
    if row is None or row["hidden"]:  # a hidden document stays out of the search until it is shown again
        return
    tags = " ".join(r["name"] for r in conn.execute(
        "SELECT t.name FROM doc_tags dt JOIN tags t ON t.id = dt.tag_id WHERE dt.doc_id = ?", (doc_id,)))
    folders = " ".join(r["folder"] for r in conn.execute("SELECT DISTINCT folder FROM locations WHERE doc_id = ?", (doc_id,)))
    conn.execute("INSERT INTO docs_fts(rowid, title, body, filename, tags, folder) VALUES (?, ?, ?, ?, ?, ?)",
                 (doc_id, row["title"], row["text"], row["original_name"], tags, folders))


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
    def __init__(self, config: Config, db: Database, rules: RulesStore, embedder: Embedder, vault=None):
        self.config = config
        self.db = db
        self.rules = rules
        self.embedder = embedder
        self.vault = vault

    # ---------------------------------------------------------------- where the bytes are

    def source_path(self, doc_id: int) -> Path | None:
        """The first existing location of a document, inside the source folder (never outside it)."""
        root = self.config.source_dir
        for r in self.db.conn.execute("SELECT path FROM locations WHERE doc_id = ? ORDER BY id", (doc_id,)):
            if ".." in r["path"].split("/"):
                continue
            path = root / r["path"]
            if path.is_file():
                return path
        return None

    def path_of(self, row) -> Path | None:
        """The file to read: the decrypted copy of a protected PDF, else the document where the user keeps it."""
        if row["work_path"]:
            path = self.config.data_dir / row["work_path"]
            return path if path.is_file() else None
        return self.source_path(row["id"])

    # ---------------------------------------------------------------- steps that the reanalysis reuses

    def retype(self, doc_id: int) -> list[str]:
        row = self.db.conn.execute("SELECT title, text, original_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
        matched = self.rules.rules.match(row["text"], row["title"] + " " + row["original_name"])
        names = [rule.name for rule, _score in matched]
        self.db.conn.execute("UPDATE documents SET types = ? WHERE id = ?", (json.dumps(names, ensure_ascii=False), doc_id))
        self.auto_tags(doc_id)
        return names

    def content(self, doc_id: int, text: str, title: str) -> tuple[list[str], str]:
        """Distinctive keywords and the extractive summary of a document, against what the archive already holds."""
        counts, forms = summarizer.term_stats(text)
        if not counts:
            return [], summarizer.extractive_summary(text, {})
        conn = self.db.conn
        n_docs = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'done' AND id != ?", (doc_id,)).fetchone()[0]
        terms = list(counts)
        freq: dict[str, int] = {}
        for i in range(0, len(terms), 400):
            chunk = terms[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT term, doc FROM docs_vocab WHERE col = 'body' AND term IN ({marks})", chunk):
                freq[r["term"]] = r["doc"]
        # the document itself is in the index when it is analysed again: it is not "another document" holding the word
        if conn.execute("SELECT 1 FROM docs_fts WHERE rowid = ?", (doc_id,)).fetchone():
            freq = {t: max(0, n - 1) for t, n in freq.items()}
        scores = summarizer.term_scores(counts, freq, n_docs)
        return (summarizer.keywords(text, title, counts, forms, scores), summarizer.extractive_summary(text, scores))

    def auto_tags(self, doc_id: int) -> None:
        """Year, types, the main entity's name, the rules' tags and the document's own keywords; tags added by hand
        are never removed."""
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
        found, digest = self.content(doc_id, row["text"], row["title"])
        names.extend(found)
        conn.execute("UPDATE documents SET summary = ? WHERE id = ?", (digest, doc_id))
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

    # ---------------------------------------------------------------- the whole analysis

    def _set_status(self, doc_id: int, status: str, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.db.tx():
            self.db.conn.execute(f"UPDATE documents SET status = ?, updated_at = ?{', ' + sets if sets else ''} WHERE id = ?",
                                 [status, now(), *fields.values(), doc_id])

    def process(self, doc_id: int, progress=None) -> None:
        conn = self.db.conn
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None or row["status"] in ("password_required", "missing"):
            return
        if row["work_path"] and not (self.config.data_dir / row["work_path"]).is_file():
            # the decrypted copy is gone (data folder restored without it): decrypt again, with the saved password
            self.db.conn.execute("UPDATE documents SET work_path = '' WHERE id = ?", (doc_id,))
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        path = self.path_of(row)
        if path is None:
            if not row["work_path"] and not conn.execute("SELECT 1 FROM locations WHERE doc_id = ?", (doc_id,)).fetchone():
                self._set_status(doc_id, "missing", missing_at=now())
                return
            raise extract.ExtractError("fichier introuvable sur le disque (déplacé pendant l'analyse ? il sera repris au prochain scan)")
        cfg = self.config

        if row["ext"] == "pdf" and not row["work_path"]:
            kind = pdfcrypt.inspect(path)
            if kind == pdfcrypt.USER_PASSWORD:
                copy = cfg.unlocked_dir / f"{row['uuid']}.pdf"
                for password in (self.vault.candidates(row["sha256"]) if self.vault else []):
                    if pdfcrypt.decrypt(path, copy, password):  # a password typed before opens it: nothing to ask
                        self.vault.learned(row["sha256"], password)
                        self._set_status(doc_id, "processing", work_path=f"unlocked/{copy.name}", unlocked_at=now())
                        path = copy
                        break
                else:
                    self._set_status(doc_id, "password_required")  # waits for the password, neither read nor indexed
                    return
            if kind == pdfcrypt.OWNER_ONLY:
                # restrictions only (no password to open): a restriction-free copy under /data, silently
                copy = cfg.unlocked_dir / f"{row['uuid']}.pdf"
                pdfcrypt.decrypt(path, copy)
                self._set_status(doc_id, "processing", work_path=f"unlocked/{copy.name}", unlocked_at=now())
                path = copy

        result = extract.extract(path, row["ext"], cfg.ocr_langs, cfg.ocr_threads, cfg.ocr_max_pages, progress)
        text, _masked = identifiers.mask_sensitive(result.text)

        if progress:
            progress("Analyse")
        mtime = conn.execute("SELECT mtime_ns FROM locations WHERE doc_id = ? ORDER BY id LIMIT 1", (doc_id,)).fetchone()
        fallback = (dt.date.fromtimestamp(mtime["mtime_ns"] / 1e9) if mtime else dt.date.fromisoformat(row["created_at"][:10]))
        if fallback.year < 1980:
            fallback = dt.date.today()
        if row["date_source"] == "manual" and row["doc_date"]:
            doc_date, source = row["doc_date"], "manual"
        else:
            found = dates.detect(text, today=max(dt.date.today(), fallback))
            doc_date, source = (found.isoformat(), "detected") if found else (fallback.isoformat(), "file")

        title = row["title"]
        if not row["title_manual"] and summarizer.is_junk_name(Path(row["original_name"]).stem):
            title = summarizer.derive_title(text) or title  # scan0012.pdf → the heading the document carries

        with self.db.tx():
            conn.execute("UPDATE documents SET title = ?, text = ?, pages = ?, ocr = ?, doc_date = ?, date_source = ?, "
                         "updated_at = ? WHERE id = ?", (title, text, result.pages, int(result.ocr), doc_date, source, now(), doc_id))
            conn.execute("DELETE FROM doc_keys WHERE doc_id = ?", (doc_id,))
            for key in identifiers.extract(text, int(doc_date[:4])):
                conn.execute("INSERT OR IGNORE INTO doc_keys(doc_id, key, value, label) VALUES (?, ?, ?, ?)",
                             (doc_id, key.key, key.value, key.label))
            self.retype(doc_id)

        if progress:
            progress("Calcul de l'empreinte sémantique")
        self.embed(doc_id)
        with self.db.tx():
            self.auto_tags(doc_id)

        if progress:
            progress("Vignette")
        thumbs.make(path, row["ext"], cfg.thumbs_dir / f"{row['uuid']}.webp", text)
        self._set_status(doc_id, "done", error=None, processed_at=now(), attempts=0)


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

    def recover_interrupted(self) -> None:
        """Documents left « processing » by a stop or a crash go back to the queue; one that was already tried and cut
        short twice (the process was killed on it: out of memory, say) is set aside as an error instead of looping."""
        with self.db.tx():
            self.db.conn.execute("UPDATE documents SET status = 'error', updated_at = ?, error = ? WHERE status = 'processing' "
                                 "AND attempts >= 2", (now(), "L'analyse de ce fichier a interrompu Papyrus deux fois de suite "
                                                       "(mémoire insuffisante ?) : il est mis de côté. « Relancer l'analyse » pour réessayer."))
            self.db.conn.execute("UPDATE documents SET status = 'pending' WHERE status = 'processing'")

    def start(self) -> None:
        self.recover_interrupted()
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
            conn.execute("UPDATE documents SET status = 'processing', attempts = attempts + 1 WHERE id = ?", (doc_id,))
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
            trim_memory()  # give what the analysis freed back to the system (glibc keeps it otherwise)
        return True

    def _embed_missing(self) -> None:
        """When the model becomes available after documents were processed without it."""
        if not self.processor.embedder.available or self.embedded_version == self.processor.embedder.version:
            return
        self.embedded_version = self.processor.embedder.version
        missing = [r["id"] for r in self.db.conn.execute("SELECT id FROM documents WHERE status = 'done' AND embedding IS NULL")]
        for doc_id in missing:
            if self._stop.is_set():
                return
            self.current = {"id": doc_id, "title": "", "step": "Calcul de l'empreinte sémantique", "started": time.time()}
            try:
                with self.db.tx():
                    self.processor.embed(doc_id)
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


__all__ = ["Processor", "Worker", "index_document", "add_tag", "prune_tags", "reembed_all", "posixpath"]
