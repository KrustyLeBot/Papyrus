"""What can be done to a document besides reading it: unlock a protected PDF, remove it from Papyrus (the file on
disk is never touched), forget what the scanner lost, bring back what was removed."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from . import pdfcrypt
from .config import Config
from .db import Database


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class Outcome:
    status: str  # "queued" | "password_required" | "rejected"
    doc_id: int | None = None
    name: str = ""
    message: str = ""


class Library:
    def __init__(self, config: Config, db: Database, source_path, vault=None):
        self.config = config
        self.db = db
        self.source_path = source_path  # doc_id -> Path | None
        self.vault = vault

    def locked(self) -> list[dict]:
        rows = self.db.conn.execute(
            "SELECT d.id, d.title, d.original_name, d.size, d.created_at, "
            "(SELECT path FROM locations l WHERE l.doc_id = d.id ORDER BY l.id LIMIT 1) AS path "
            "FROM documents d WHERE d.status = 'password_required' ORDER BY d.id")
        return [dict(r) for r in rows]

    def unlock(self, doc_id: int, password: str, remember: bool = True) -> Outcome:
        """Decrypts one waiting PDF into a copy under /data (the original stays as it is). The password is used here and
        nowhere else: it is not stored, logged or returned."""
        row = self.db.conn.execute("SELECT * FROM documents WHERE id = ? AND status = 'password_required'", (doc_id,)).fetchone()
        if row is None:
            return Outcome("rejected", doc_id, message="Ce document n'attend pas de mot de passe.")
        src = self.source_path(doc_id)
        if src is None:
            return Outcome("rejected", doc_id, row["original_name"], "Le fichier n'est plus dans le dossier analysé.")
        dst = self.config.unlocked_dir / f"{row['uuid']}.pdf"
        if not pdfcrypt.decrypt(src, dst, password):
            return Outcome("password_required", doc_id, row["original_name"], "Mot de passe incorrect.")
        with self.db.tx():
            self.db.conn.execute("UPDATE documents SET work_path = ?, status = 'pending', unlocked_at = ?, updated_at = ? WHERE id = ?",
                                 (f"unlocked/{dst.name}", now(), now(), doc_id))
        if remember and self.vault is not None:
            self.vault.remember(row["sha256"], password)  # by the document's hash, encrypted; see vault.py
        return Outcome("queued", doc_id, row["original_name"], "Déverrouillé.")

    # ---------------------------------------------------------------- removal (the file on disk stays)

    def _drop_files(self, row) -> None:
        if row["work_path"]:
            (self.config.data_dir / row["work_path"]).unlink(missing_ok=True)
        (self.config.thumbs_dir / f"{row['uuid']}.webp").unlink(missing_ok=True)

    def forget(self, doc_id: int) -> bool:
        """Removes a document from Papyrus and keeps its content out of every later scan (by SHA-256, so a copy
        elsewhere stays out too). Nothing is deleted from the source folder."""
        conn = self.db.conn
        row = conn.execute("SELECT uuid, title, sha256, work_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            return False
        first = conn.execute("SELECT path FROM locations WHERE doc_id = ? ORDER BY id LIMIT 1", (doc_id,)).fetchone()
        with self.db.tx():
            conn.execute("INSERT OR REPLACE INTO ignored(sha256, title, path, created_at) VALUES (?, ?, ?, ?)",
                         (row["sha256"], row["title"], first["path"] if first else "", now()))
            conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.execute("DELETE FROM doc_passwords WHERE sha256 = ?", (row["sha256"],))
            conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM doc_tags)")
        self._drop_files(row)
        return True

    def purge_missing(self) -> int:
        """Forgets the documents whose files are gone for good (their analysis is deleted, nothing is ignored)."""
        conn = self.db.conn
        rows = conn.execute("SELECT id, uuid, work_path FROM documents WHERE status = 'missing'").fetchall()
        with self.db.tx():
            for r in rows:
                conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (r["id"],))
                conn.execute("DELETE FROM documents WHERE id = ?", (r["id"],))
            conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM doc_tags)")
        for r in rows:
            self._drop_files(r)
        return len(rows)

    def unignore_all(self) -> int:
        with self.db.tx():
            return self.db.conn.execute("DELETE FROM ignored").rowcount
