"""Passwords of protected PDFs, kept so the user types each one once.

Each password is stored against the SHA-256 of the document it opened, encrypted at rest (Fernet: AES-128-CBC with
HMAC) with a key that is NOT in the database: PAPYRUS_KEY from the environment when set (then a copy of the data
folder alone is useless), else a random key file `secret.key` next to the database. They are tried, most recently
useful first, on every protected PDF the scanner finds later. A password is never logged, never returned by the API,
never written to an export."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

from .config import Config
from .db import Database

MAX_TRIES = 100


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Vault:
    def __init__(self, config: Config, db: Database):
        self.db = db
        self._fernet = Fernet(self._key(config))

    @staticmethod
    def _key(config: Config) -> bytes:
        env = os.environ.get("PAPYRUS_KEY", "").strip()
        if env:
            return base64.urlsafe_b64encode(hashlib.sha256(env.encode()).digest())
        path = config.data_dir / "secret.key"
        try:
            return path.read_bytes().strip()
        except FileNotFoundError:
            key = Fernet.generate_key()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
            return key

    def remember(self, sha: str, password: str) -> None:
        token = self._fernet.encrypt(password.encode("utf-8"))
        with self.db.tx():
            self.db.conn.execute(
                "INSERT INTO doc_passwords(sha256, secret, created_at, used_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(sha256) DO UPDATE SET secret = excluded.secret, used_at = excluded.used_at", (sha, token, now(), now()))

    def touch(self, sha: str) -> None:
        with self.db.tx():
            self.db.conn.execute("UPDATE doc_passwords SET used_at = ? WHERE sha256 = ?", (now(), sha))

    def candidates(self, sha: str) -> list[str]:
        """The password saved for this very document first, then every other one (distinct, recently useful first)."""
        rows = self.db.conn.execute("SELECT sha256, secret FROM doc_passwords ORDER BY (sha256 = ?) DESC, used_at DESC", (sha,)).fetchall()
        out: list[str] = []
        for r in rows:
            try:
                password = self._fernet.decrypt(r["secret"]).decode("utf-8")
            except InvalidToken:
                continue  # saved under another key (PAPYRUS_KEY changed): it cannot be read, so it is skipped
            if password not in out:
                out.append(password)
            if len(out) >= MAX_TRIES:
                break
        return out

    def count(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM doc_passwords").fetchone()[0]

    def forget_all(self) -> int:
        with self.db.tx():
            return self.db.conn.execute("DELETE FROM doc_passwords").rowcount

    def forget(self, sha: str) -> None:
        with self.db.tx():
            self.db.conn.execute("DELETE FROM doc_passwords WHERE sha256 = ?", (sha,))
