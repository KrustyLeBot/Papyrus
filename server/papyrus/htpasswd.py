"""The accounts: an Apache htpasswd file (conf/httpasswd), bcrypt hashes only (htpasswd -B). Weaker formats
(apr1/MD5, {SHA}, crypt, plain text) are refused with a clear message. The file is read again when it changes."""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import bcrypt

_DUMMY = bcrypt.hashpw(b"papyrus-timing", bcrypt.gensalt(rounds=10))


def _kind(hashed: str) -> str:
    if hashed.startswith(("$2a$", "$2b$", "$2y$")):
        return "bcrypt"
    if hashed.startswith("$apr1$"):
        return "MD5 ($apr1$)"
    if hashed.startswith("{SHA}"):
        return "SHA1 ({SHA})"
    if hashed.startswith(("$1$", "$5$", "$6$")) or len(hashed) == 13:
        return "crypt"
    return "texte en clair ou format inconnu"


def parse(text: str, source: str = "httpasswd") -> tuple[dict[str, str], list[str]]:
    users: dict[str, str] = {}
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        user, sep, hashed = line.partition(":")
        if not sep or not user or not hashed:
            problems.append(f"{source}, ligne {number} : « utilisateur:empreinte » attendu")
            continue
        kind = _kind(hashed)
        if kind != "bcrypt":
            problems.append(f"{source}, ligne {number} ({user}) : format {kind} refusé, recréer le compte avec "
                            f"htpasswd -B (bcrypt)")
            continue
        users[user] = hashed
    return users, problems


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()


class Accounts:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._users: dict[str, str] = {}
        self.problems: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        with self._lock:
            if mtime == self._mtime and (mtime is not None or not self._users):
                return
            self._mtime = mtime
            if mtime is None:
                self._users, self.problems = {}, [f"{self.path} est absent"]
            else:
                try:
                    text = self.path.read_text(encoding="utf-8")
                except OSError as error:
                    self._users, self.problems = {}, [f"{self.path} illisible ({error})"]
                    return
                self._users, self.problems = parse(text, str(self.path))
            for problem in self.problems:
                print(f"papyrus: {problem}", flush=True)
            print(f"papyrus: {len(self._users)} account(s) in {self.path}", flush=True)

    @property
    def users(self) -> dict[str, str]:
        self.refresh()
        with self._lock:
            return dict(self._users)

    def verify(self, user: str, password: str) -> bool:
        hashed = self.users.get(user)
        if hashed is None:
            bcrypt.checkpw(password.encode(), _DUMMY)  # same time whether the user exists or not
            return False
        try:
            return bcrypt.checkpw(password.encode(), ("$2b$" + hashed[4:]).encode())
        except ValueError:
            return False

    def fingerprint(self, user: str) -> str | None:
        """Changes when the user's password changes: sessions opened with the old one stop working."""
        hashed = self.users.get(user)
        return hashlib.sha256(f"{user}:{hashed}".encode()).hexdigest()[:32] if hashed else None
