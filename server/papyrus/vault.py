"""Passwords of protected PDFs, kept so the user types each one once.

The key is derived (scrypt) from the password of the user's own Papyrus account, nothing else: no key file, no
setting. Papyrus only knows that password while the user logs in, so the key lives in memory from the login until the
process stops. Consequences, on purpose:
  - a copy of the data folder alone cannot read the saved passwords;
  - after a restart, saved passwords wait for the next login (the UI says so);
  - change the account password and the saved ones cannot be read any more: they are dropped at the next login and
    the user types them again (the documents already unlocked keep their decrypted copy).
Each saved password is tied to the SHA-256 of the document it opened and to the account that saved it. It is never
logged, never returned by the API, never written to an export. Without a login (AUTH_DISABLED) nothing is kept."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from .db import Database

MAX_TRIES = 100


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Vault:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._keys: dict[str, tuple[Fernet, str | None]] = {}  # account -> (cipher, fingerprint of its password)

    # ---------------------------------------------------------------- keys

    def _salt(self, user: str) -> bytes:
        name = f"vault_salt:{user}"
        raw = self.db.setting(name)
        if raw:
            return bytes.fromhex(raw)
        salt = os.urandom(16)
        with self.db.tx():
            self.db.set_setting(name, salt.hex())
        return salt

    def unlock(self, user: str, password: str, fingerprint: str | None = None) -> int:
        """Derives the account's key from its password and keeps it in memory. Passwords saved under a different key
        (the account password was changed) cannot be read any more and are dropped: returns how many."""
        derived = hashlib.scrypt(password.encode("utf-8"), salt=self._salt(user), n=2 ** 14, r=8, p=1, dklen=32)
        cipher = Fernet(base64.urlsafe_b64encode(derived))
        dropped = 0
        for r in self.db.conn.execute("SELECT sha256, secret FROM doc_passwords WHERE owner = ?", (user,)).fetchall():
            try:
                cipher.decrypt(r["secret"])
            except InvalidToken:
                with self.db.tx():
                    self.db.conn.execute("DELETE FROM doc_passwords WHERE owner = ? AND sha256 = ?", (user, r["sha256"]))
                dropped += 1
        with self._lock:
            self._keys[user] = (cipher, fingerprint)
        if dropped:
            print(f"papyrus: {dropped} saved PDF password(s) of {user} could not be read with the new account password and were dropped", flush=True)
        return dropped

    def is_unlocked(self, user: str, fingerprint: str | None = None) -> bool:
        with self._lock:
            held = self._keys.get(user)
        return held is not None and (fingerprint is None or held[1] == fingerprint)

    def status(self) -> dict:
        """saved: passwords kept; locked: some belong to an account nobody logged in as since the process started."""
        rows = self.db.conn.execute("SELECT owner, COUNT(*) AS n FROM doc_passwords GROUP BY owner").fetchall()
        with self._lock:
            held = set(self._keys)
        return {"saved": sum(r["n"] for r in rows), "locked": any(r["owner"] not in held for r in rows),
                "unlocked_accounts": len(held)}

    # ---------------------------------------------------------------- saving and trying

    def remember(self, owner: str, sha: str, password: str) -> bool:
        """Saves the password for this document under the account's key; False when that account's key is not in memory
        (AUTH_DISABLED, or a session that outlived a restart: logging in again brings the key back)."""
        with self._lock:
            held = self._keys.get(owner)
        if held is None:
            return False
        token = held[0].encrypt(password.encode("utf-8"))
        with self.db.tx():
            self.db.conn.execute(
                "INSERT INTO doc_passwords(owner, sha256, secret, created_at, used_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(owner, sha256) DO UPDATE SET secret = excluded.secret, used_at = excluded.used_at",
                (owner, sha, token, now(), now()))
        return True

    def learned(self, sha: str, password: str) -> None:
        """A saved password opened this document by itself: it is tied to the document too, for every account whose key
        is in memory."""
        with self._lock:
            owners = list(self._keys)
        for owner in owners:
            self.remember(owner, sha, password)

    def candidates(self, sha: str) -> list[str]:
        """Every password that can be read now (accounts whose key is in memory): the one saved for this very document
        first, then the others, recently useful first, without duplicates."""
        with self._lock:
            held = dict(self._keys)
        if not held:
            return []
        marks = ",".join("?" * len(held))
        rows = self.db.conn.execute(
            f"SELECT owner, secret FROM doc_passwords WHERE owner IN ({marks}) ORDER BY (sha256 = ?) DESC, used_at DESC",
            [*held, sha]).fetchall()
        out: list[str] = []
        for r in rows:
            try:
                password = held[r["owner"]][0].decrypt(r["secret"]).decode("utf-8")
            except InvalidToken:
                continue
            if password not in out:
                out.append(password)
            if len(out) >= MAX_TRIES:
                break
        return out

    def count(self) -> int:
        return self.db.conn.execute("SELECT COUNT(DISTINCT sha256) FROM doc_passwords").fetchone()[0]

    def forget_all(self) -> int:
        with self.db.tx():
            return self.db.conn.execute("DELETE FROM doc_passwords").rowcount

    def forget(self, sha: str) -> None:
        with self.db.tx():
            self.db.conn.execute("DELETE FROM doc_passwords WHERE sha256 = ?", (sha,))
