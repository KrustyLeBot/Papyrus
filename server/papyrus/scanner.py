"""The scanner: walks the source folder (read-only), follows every PDF/DOCX by the SHA-256 of its content and keeps
the database in line with the disk, with no import step.

- a new content becomes a document waiting for analysis;
- a file that moved or was renamed keeps its document (same SHA-256, its location is updated, everything learned
  and everything typed by hand stays);
- the same content at two paths is one document with two locations;
- a file edited in place keeps its document too (new content, analysed again, tags and title typed by hand kept);
- a file that vanished leaves its document « missing » (hidden, nothing deleted): it comes back by itself when the
  file does, so an unplugged disk or a mistaken move never costs the archive.
Unchanged files (same size and modification time) are not read again."""
from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import os
import posixpath
import threading
import time
import traceback
import uuid as uuidlib
from pathlib import Path

from . import extract, summary as summarizer
from .config import Config
from .db import Database
from .pipeline import index_document
from .text import clean_title


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Scanner:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.on_new_work = lambda: None
        self.state: dict = {"running": False, "started": None, "files": 0, "hashed": 0, "warning": None, "last": None}

    # ---------------------------------------------------------------- thread

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="papyrus-scanner", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def scan_now(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        self._stop.wait(3)  # let the server come up first
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:  # noqa: BLE001 - the scanner must outlive any one bad scan
                traceback.print_exc()
                self.state.update(running=False, warning="L'analyse du dossier a échoué (voir les logs).")
            self._wake.wait(timeout=self.config.scan_interval)
            self._wake.clear()

    # ---------------------------------------------------------------- walking

    def excluded(self, name: str) -> bool:
        low = name.lower()
        return any(fnmatch.fnmatchcase(low, pattern.lower()) for pattern in self.config.exclude)

    def walk(self, root: Path, failed: list[str]):
        """(relative posix path, size, mtime_ns) of every file of a wanted kind. Symbolic links are not followed."""
        stack: list[tuple[str, str]] = [("", str(root))]
        wanted = self.config.extensions
        while stack:
            rel_dir, directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as error:
                failed.append(rel_dir)
                print(f"papyrus: cannot read {directory or '/'} ({error.__class__.__name__})", flush=True)
                continue
            for entry in entries:
                name = entry.name
                if self.excluded(name):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    rel = f"{rel_dir}/{name}" if rel_dir else name
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((rel, entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False) or "." not in name:
                        continue
                    if name.rsplit(".", 1)[-1].lower() not in wanted:
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                yield rel, st.st_size, st.st_mtime_ns

    # ---------------------------------------------------------------- the scan

    def scan(self) -> dict:
        with self._lock:
            return self._scan()

    def _scan(self) -> dict:
        cfg, conn = self.config, self.db.conn
        started = time.time()
        self.state.update(running=True, started=started, files=0, hashed=0, warning=None)
        root = cfg.source_dir
        stats = {"new": 0, "relocated": 0, "updated": 0, "removed": 0, "missing": 0, "restored": 0, "unchanged": 0,
                 "ignored": 0, "skipped": 0, "unreadable": 0, "waiting": 0}
        try:
            if not root.is_dir():
                self.state["warning"] = f"Dossier source introuvable : {root}. Le montage est-il en place ?"
                return self._finish(stats, started)
            generation = int(self.db.setting("scan_gen") or 0) + 1
            self.db.set_setting("scan_gen", str(generation))
            failed: list[str] = []
            files = list(self.walk(root, failed))
            self.state["files"] = len(files)
            known = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
            if not files and known:
                # an empty source while documents are known looks like a missing mount, not a deleted archive
                self.state["warning"] = (f"Aucun fichier trouvé dans {root} alors que {known} emplacements sont connus : "
                                         "dossier vide ou disque non monté ? Rien n'est retiré.")
                return self._finish(stats, started)
            locations = {r["path"]: r for r in conn.execute("SELECT id, doc_id, path, size, mtime_ns FROM locations")}
            ignored = {r["sha256"] for r in conn.execute("SELECT sha256 FROM ignored")}
            touched: set[int] = set()
            unchanged_ids: list[int] = []
            limit = cfg.max_file_mb * 1024 * 1024
            for rel, size, mtime_ns in files:
                if self._stop.is_set():
                    return self._finish(stats, started)
                loc = locations.get(rel)
                if loc and loc["size"] == size and loc["mtime_ns"] == mtime_ns:
                    unchanged_ids.append(loc["id"])
                    stats["unchanged"] += 1
                    continue
                if 0 <= time.time() - mtime_ns / 1e9 < cfg.settle_seconds:  # a date in the future is a clock gap, not a copy
                    stats["waiting"] += 1  # still being copied or saved: next scan
                    if loc:
                        unchanged_ids.append(loc["id"])
                    continue
                if size > limit:
                    stats["skipped"] += 1
                    continue
                try:
                    sha = sha256_file(root / rel)
                except OSError:
                    stats["unreadable"] += 1
                    if loc:
                        unchanged_ids.append(loc["id"])
                    continue
                self.state["hashed"] += 1
                self._apply(rel, size, mtime_ns, sha, loc, generation, ignored, stats, touched)
            for i in range(0, len(unchanged_ids), 500):
                chunk = unchanged_ids[i:i + 500]
                with self.db.tx():
                    conn.execute(f"UPDATE locations SET seen = ? WHERE id IN ({','.join('?' * len(chunk))})", [generation, *chunk])
            self._remove_vanished(generation, failed, stats, touched)
            self._refresh(touched)
            return self._finish(stats, started)
        finally:
            self.state["running"] = False

    def _finish(self, stats: dict, started: float) -> dict:
        stats = dict(stats, at=now(), seconds=round(time.time() - started, 1))
        self.state["last"] = stats
        self.state["running"] = False
        if stats["new"] or stats["updated"] or stats["restored"]:
            self.on_new_work()
        return stats

    def _apply(self, rel, size, mtime_ns, sha, loc, generation, ignored, stats, touched) -> None:
        conn = self.db.conn
        folder = posixpath.dirname(rel)
        name = posixpath.basename(rel)
        with self.db.tx():
            if sha in ignored:
                stats["ignored"] += 1
                if loc:
                    conn.execute("DELETE FROM locations WHERE id = ?", (loc["id"],))
                    touched.add(loc["doc_id"])
                return
            doc = conn.execute("SELECT id, status, processed_at FROM documents WHERE sha256 = ?", (sha,)).fetchone()
            if doc:
                if loc and loc["doc_id"] != doc["id"]:
                    touched.add(loc["doc_id"])
                    conn.execute("UPDATE locations SET doc_id = ?, size = ?, mtime_ns = ?, seen = ? WHERE id = ?",
                                 (doc["id"], size, mtime_ns, generation, loc["id"]))
                elif loc:
                    conn.execute("UPDATE locations SET size = ?, mtime_ns = ?, seen = ? WHERE id = ?", (size, mtime_ns, generation, loc["id"]))
                else:
                    conn.execute("INSERT INTO locations(doc_id, path, folder, size, mtime_ns, seen) VALUES (?,?,?,?,?,?)",
                                 (doc["id"], rel, folder, size, mtime_ns, generation))
                    stats["relocated"] += 1
                touched.add(doc["id"])
                if doc["status"] == "missing":
                    conn.execute("UPDATE documents SET status = ?, missing_at = NULL, updated_at = ? WHERE id = ?",
                                 ("done" if doc["processed_at"] else "pending", now(), doc["id"]))
                    stats["restored"] += 1
                return
            if loc:
                others = conn.execute("SELECT COUNT(*) FROM locations WHERE doc_id = ? AND id != ?", (loc["doc_id"], loc["id"])).fetchone()[0]
                if not others:
                    # edited in place: the same document with new content, analysed again; what was typed by hand stays
                    old = conn.execute("SELECT work_path FROM documents WHERE id = ?", (loc["doc_id"],)).fetchone()
                    if old and old["work_path"]:
                        (self.config.data_dir / old["work_path"]).unlink(missing_ok=True)
                    conn.execute("UPDATE documents SET sha256 = ?, size = ?, work_path = '', status = 'pending', error = NULL, "
                                 "embedding = NULL, missing_at = NULL, updated_at = ? WHERE id = ?", (sha, size, now(), loc["doc_id"]))
                    conn.execute("UPDATE locations SET size = ?, mtime_ns = ?, seen = ? WHERE id = ?", (size, mtime_ns, generation, loc["id"]))
                    touched.add(loc["doc_id"])
                    stats["updated"] += 1
                    return
            ext = name.rsplit(".", 1)[-1].lower()
            stamp = now()
            cur = conn.execute(
                "INSERT INTO documents(uuid, title, original_name, ext, mime, size, sha256, status, created_at, updated_at, doc_date, "
                "date_source) VALUES (?,?,?,?,?,?,?, 'pending', ?, ?, ?, 'file')",
                (uuidlib.uuid4().hex, clean_title(name), name, ext, extract.MIME.get(ext, "application/octet-stream"), size, sha,
                 stamp, stamp, _day(mtime_ns)))
            doc_id = cur.lastrowid
            if loc:
                conn.execute("UPDATE locations SET doc_id = ?, size = ?, mtime_ns = ?, seen = ? WHERE id = ?",
                             (doc_id, size, mtime_ns, generation, loc["id"]))
                touched.add(loc["doc_id"])
            else:
                conn.execute("INSERT INTO locations(doc_id, path, folder, size, mtime_ns, seen) VALUES (?,?,?,?,?,?)",
                             (doc_id, rel, folder, size, mtime_ns, generation))
            touched.add(doc_id)
            stats["new"] += 1

    def _remove_vanished(self, generation, failed, stats, touched) -> None:
        conn = self.db.conn

        def under_failed(path: str) -> bool:
            return any(not f or path == f or path.startswith(f + "/") for f in failed)  # a folder we could not read keeps its rows

        gone = [r for r in conn.execute("SELECT id, doc_id, path FROM locations WHERE seen != ?", (generation,))
                if not under_failed(r["path"])]
        if gone:
            with self.db.tx():
                for r in gone:
                    conn.execute("DELETE FROM locations WHERE id = ?", (r["id"],))
                    touched.add(r["doc_id"])
            stats["removed"] += len(gone)
        orphans = conn.execute("SELECT id FROM documents WHERE status != 'missing' AND NOT EXISTS "
                               "(SELECT 1 FROM locations WHERE doc_id = documents.id)").fetchall()
        if orphans:
            with self.db.tx():
                for r in orphans:
                    conn.execute("UPDATE documents SET status = 'missing', missing_at = ?, updated_at = ? WHERE id = ?", (now(), now(), r["id"]))
                    conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (r["id"],))
            stats["missing"] += len(orphans)

    def _refresh(self, touched: set[int]) -> None:
        """Names, titles and the search index of documents whose locations changed."""
        conn = self.db.conn
        for doc_id in touched:
            row = conn.execute("SELECT id, original_name, title, title_manual, status, processed_at FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if row is None or row["status"] == "missing":
                continue
            first = conn.execute("SELECT path FROM locations WHERE doc_id = ? ORDER BY id LIMIT 1", (doc_id,)).fetchone()
            with self.db.tx():
                if first:
                    name = posixpath.basename(first["path"])
                    if name != row["original_name"]:
                        title = row["title"]
                        if not row["title_manual"] and not summarizer.is_junk_name(Path(name).stem):
                            title = clean_title(name)  # renamed: the title follows a meaningful name
                        conn.execute("UPDATE documents SET original_name = ?, title = ? WHERE id = ?", (name, title, doc_id))
                if row["processed_at"]:
                    index_document(self.db, doc_id)


def _day(mtime_ns: int) -> str:
    try:
        day = dt.date.fromtimestamp(mtime_ns / 1e9)
        return day.isoformat() if day.year >= 1980 else dt.date.today().isoformat()
    except (OverflowError, OSError, ValueError):
        return dt.date.today().isoformat()
