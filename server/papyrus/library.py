"""Intake (upload, duplicates, protected PDFs) and removal of documents. The files live under
files/YYYY/MM/<uuid>.<ext> (by upload date, opaque names); protected PDFs wait in locked/."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import uuid as uuidlib
from dataclasses import dataclass
from pathlib import Path

from . import extract, pdfcrypt
from .config import Config
from .db import Database
from .grouping import Grouper, now
from .text import clean_title


@dataclass
class Intake:
    status: str  # "queued" | "duplicate" | "password_required" | "rejected"
    doc_id: int | None = None
    name: str = ""
    message: str = ""
    title: str = ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Library:
    def __init__(self, config: Config, db: Database, grouper: Grouper):
        self.config = config
        self.db = db
        self.grouper = grouper

    def tmp_dir(self) -> Path:
        path = self.config.data_dir / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def clean_tmp(self) -> None:
        shutil.rmtree(self.config.data_dir / "tmp", ignore_errors=True)

    def _duplicate(self, *hashes: str) -> int | None:
        for h in hashes:
            if not h:
                continue
            row = self.db.conn.execute("SELECT id FROM documents WHERE sha256 = ? OR sha256_encrypted = ? LIMIT 1", (h, h)).fetchone()
            if row:
                return row["id"]
        return None

    def _final_path(self, doc_uuid: str, ext: str) -> tuple[Path, str]:
        today = dt.date.today()
        rel = f"files/{today:%Y}/{today:%m}/{doc_uuid}.{ext}"
        path = self.config.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        return path, rel

    def ingest(self, tmp: Path, original_name: str) -> Intake:
        """Takes a file written to tmp/ (moved or deleted, never left behind)."""
        name = os.path.basename(original_name.replace("\\", "/"))[:200] or "document"
        try:
            with tmp.open("rb") as handle:
                head = handle.read(8192)
            ext = extract.sniff_ext(head, name)
            if ext is None:
                return Intake("rejected", name=name, message="Format non pris en charge (PDF, image, DOCX, TXT).")
            upload_sha = sha256_file(tmp)
            existing = self._duplicate(upload_sha)
            if existing:
                return Intake("duplicate", existing, name, "Déjà présent dans Papyrus.", self._title(existing))
            doc_uuid = uuidlib.uuid4().hex
            title = clean_title(name)
            size = tmp.stat().st_size
            if ext == "pdf":
                kind = pdfcrypt.inspect(tmp)
                if kind == pdfcrypt.USER_PASSWORD:
                    locked = self.config.locked_dir / f"{doc_uuid}.pdf"
                    shutil.move(tmp, locked)
                    doc_id = self._insert(doc_uuid, title, name, ext, size, upload_sha, upload_sha, f"locked/{doc_uuid}.pdf",
                                          "password_required")
                    return Intake("password_required", doc_id, name, "Ce PDF est protégé par un mot de passe.", title)
                if kind == pdfcrypt.OWNER_ONLY:
                    final, rel = self._final_path(doc_uuid, ext)
                    pdfcrypt.decrypt(tmp, final)
                    clear_sha = sha256_file(final)
                    existing = self._duplicate(clear_sha)
                    if existing:
                        final.unlink(missing_ok=True)
                        return Intake("duplicate", existing, name, "Déjà présent dans Papyrus.", self._title(existing))
                    doc_id = self._insert(doc_uuid, title, name, ext, final.stat().st_size, clear_sha, upload_sha, rel,
                                          "pending", unlocked=True)
                    return Intake("queued", doc_id, name, "Restrictions du PDF retirées.", title)
            final, rel = self._final_path(doc_uuid, ext)
            shutil.move(tmp, final)
            doc_id = self._insert(doc_uuid, title, name, ext, size, upload_sha, None, rel, "pending")
            return Intake("queued", doc_id, name, "", title)
        finally:
            tmp.unlink(missing_ok=True)

    def _title(self, doc_id: int) -> str:
        row = self.db.conn.execute("SELECT title FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return row["title"] if row else ""

    def _insert(self, doc_uuid, title, name, ext, size, sha, sha_enc, rel, status, unlocked=False) -> int:
        stamp = now()
        with self.db.tx():
            cur = self.db.conn.execute(
                "INSERT INTO documents(uuid, title, original_name, ext, mime, size, sha256, sha256_encrypted, path, status, "
                "unlocked_at, created_at, updated_at, doc_date, date_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'upload')",
                (doc_uuid, title, name, ext, extract.MIME.get(ext, "application/octet-stream"), size, sha, sha_enc, rel,
                 status, stamp if unlocked else None, stamp, stamp, stamp[:10]))
            doc_id = cur.lastrowid
            if status != "password_required":
                self.grouper._ensure_somewhere(doc_id)
        return doc_id

    # ---------------------------------------------------------------- protected PDFs

    def locked(self) -> list[dict]:
        return [dict(r) for r in self.db.conn.execute(
            "SELECT id, title, original_name, size, created_at FROM documents WHERE status = 'password_required' ORDER BY id")]

    def unlock(self, doc_id: int, password: str) -> Intake:
        """Decrypts one waiting PDF. The password is used here and nowhere else."""
        row = self.db.conn.execute("SELECT * FROM documents WHERE id = ? AND status = 'password_required'", (doc_id,)).fetchone()
        if row is None:
            return Intake("rejected", doc_id, message="Ce document n'attend pas de mot de passe.")
        src = self.config.data_dir / row["path"]
        final, rel = self._final_path(row["uuid"], "pdf")
        if not pdfcrypt.decrypt(src, final, password):
            return Intake("password_required", doc_id, row["original_name"], "Mot de passe incorrect.", row["title"])
        clear_sha = sha256_file(final)
        existing = self._duplicate(clear_sha)
        if existing and existing != doc_id:
            final.unlink(missing_ok=True)
            self.delete(doc_id)
            return Intake("duplicate", existing, row["original_name"], "Déjà présent dans Papyrus.", self._title(existing))
        stamp = now()
        with self.db.tx():
            self.db.conn.execute(
                "UPDATE documents SET path = ?, sha256 = ?, size = ?, status = 'pending', unlocked_at = ?, updated_at = ? "
                "WHERE id = ?", (rel, clear_sha, final.stat().st_size, stamp, stamp, doc_id))
            self.grouper._ensure_somewhere(doc_id)
        src.unlink(missing_ok=True)
        return Intake("queued", doc_id, row["original_name"], "Déverrouillé.", row["title"])

    # ---------------------------------------------------------------- removal

    def delete(self, doc_id: int) -> bool:
        conn = self.db.conn
        row = conn.execute("SELECT uuid, path FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            return False
        with self.db.tx():
            groups = [r["group_id"] for r in conn.execute("SELECT group_id FROM doc_groups WHERE doc_id = ?", (doc_id,))]
            conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM doc_tags)")
            self.grouper.update_centroids(g for g in groups if conn.execute("SELECT 1 FROM groups WHERE id = ?", (g,)).fetchone())
            self.grouper.prune_empty()
        (self.config.data_dir / row["path"]).unlink(missing_ok=True)
        (self.config.thumbs_dir / f"{row['uuid']}.webp").unlink(missing_ok=True)
        return True
