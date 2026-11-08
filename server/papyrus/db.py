"""SQLite storage: one file in the data folder, WAL mode, FTS5 for the full-text index.

One connection per thread (the API's thread pool, the worker): SQLite serialises the writers itself, and
busy_timeout makes a writer wait instead of failing while another one commits."""
from __future__ import annotations

import shutil
import sqlite3
import threading
from urllib.parse import quote
from contextlib import contextmanager
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 2

SCHEMA = """
-- one row per distinct file content (SHA-256); the files themselves stay where the user keeps them
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    uuid            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    title_manual    INTEGER NOT NULL DEFAULT 0,
    original_name   TEXT NOT NULL,
    ext             TEXT NOT NULL,
    mime            TEXT NOT NULL DEFAULT '',
    size            INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT NOT NULL UNIQUE,
    work_path       TEXT NOT NULL DEFAULT '',   -- decrypted copy under /data (protected PDFs), else ''
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending processing done error password_required missing
    error           TEXT,
    text            TEXT NOT NULL DEFAULT '',
    pages           INTEGER NOT NULL DEFAULT 0,
    ocr             INTEGER NOT NULL DEFAULT 0,
    doc_date        TEXT,
    date_source     TEXT NOT NULL DEFAULT 'file',
    types           TEXT NOT NULL DEFAULT '[]',
    summary         TEXT NOT NULL DEFAULT '',
    embedding       BLOB,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    processed_at    TEXT,
    unlocked_at     TEXT,
    missing_at      TEXT,
    hidden          INTEGER NOT NULL DEFAULT 0   -- hidden by the user: kept, analysed, out of lists and search
);
CREATE INDEX IF NOT EXISTS documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS documents_date ON documents(doc_date);

-- where a document is: one row per file path (relative to the source folder). Several paths = several copies.
CREATE TABLE IF NOT EXISTS locations (
    id       INTEGER PRIMARY KEY,
    doc_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    path     TEXT NOT NULL UNIQUE,
    folder   TEXT NOT NULL,
    size     INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    seen     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS locations_doc ON locations(doc_id);
CREATE INDEX IF NOT EXISTS locations_folder ON locations(folder);

-- documents the user removed from Papyrus: never indexed again, wherever a copy sits
CREATE TABLE IF NOT EXISTS ignored (
    sha256     TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_keys (
    doc_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    label   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (doc_id, key, value)
);

-- passwords of protected PDFs, by the SHA-256 of the document they opened and the account that saved them;
-- encrypted with a key derived from that account's password (vault.py)
CREATE TABLE IF NOT EXISTS doc_passwords (
    owner      TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    secret     BLOB NOT NULL,
    created_at TEXT NOT NULL,
    used_at    TEXT NOT NULL,
    PRIMARY KEY (owner, sha256)
);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS doc_tags (
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'auto',
    PRIMARY KEY (doc_id, tag_id)
);
CREATE INDEX IF NOT EXISTS doc_tags_tag ON doc_tags(tag_id);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user       TEXT NOT NULL,
    created    REAL NOT NULL,
    expires    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title, body, filename, tags, folder,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- every word of the index with its document count: the IDF of the keyword scores, and the spelling corrector
CREATE VIRTUAL TABLE IF NOT EXISTS docs_vocab USING fts5vocab(docs_fts, col);
"""


NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "afs", "ncpfs", "davfs", "fuse.sshfs", "fuse.rclone", "fuse.s3fs",
              "fuse.gcsfuse", "ceph", "glusterfs", "fuse.glusterfs"}


def network_filesystem(path: Path, mountinfo: str = "/proc/self/mountinfo") -> str | None:
    """The type of the network file system (cifs, nfs…) holding `path`, None for a local disk. A bind mount of a host
    share shows up here with the share's type."""
    try:
        resolved = str(path.resolve())
        best, kind = "", None
        for line in Path(mountinfo).read_text().splitlines():
            left, _, right = line.partition(" - ")
            fields = left.split()
            point = fields[4].replace("\\040", " ")
            if (resolved == point or resolved.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
                best, kind = point, right.split()[0]
        return kind if kind in NETWORK_FS else None
    except OSError:
        return None


class Database:
    """`journal`: "auto" (WAL on a local disk, DELETE on a network share), "wal" or "delete".

    WAL needs shared memory that SMB and NFS do not provide: on a share it ends in « database is locked » or a corrupt file.
    DELETE (the classic rollback journal) works there, with synchronous=FULL so a cut connection cannot leave it broken. It is
    slower to write and a writer blocks readers for an instant, which a personal archive does not notice. One Papyrus instance
    per database folder, always."""

    def __init__(self, path: Path, journal: str = "auto", locking: str = "auto"):
        self.path = path
        self.vfs: str | None = None  # "unix-dotfile" when the share refuses file locks
        share = network_filesystem(path.parent)
        self.journal = ("delete" if share else "wal") if journal == "auto" else journal
        self.network = share
        if self.network and self.journal == "delete":
            print(f"papyrus: the data folder is on a network share ({share}): SQLite uses journal_mode=DELETE, synchronous=FULL", flush=True)
        elif self.network:
            print(f"papyrus: WARNING the data folder is on a network share ({share}) and journal_mode=WAL was forced: expect lock errors", flush=True)
        self._choose_locking(locking)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        with self.connect() as conn:
            old = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'groups'").fetchone()
            if old:
                raise SystemExit(f"papyrus: {path} comes from an older Papyrus (upload and groups). The folders model starts "
                                 "from a new data folder: point /data at an empty one.")
            if "owner" not in {r["name"] for r in conn.execute("PRAGMA table_info(doc_passwords)")} and \
                    conn.execute("SELECT name FROM sqlite_master WHERE name = 'doc_passwords'").fetchone():
                conn.execute("DROP TABLE doc_passwords")  # saved under a key file, unreadable by the account-password key anyway
            conn.executescript(SCHEMA)
            if "hidden" not in {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}:  # a database from before « Masqués »
                conn.execute("ALTER TABLE documents ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    @property
    def locking(self) -> str:
        return "dotfile" if self.vfs else "posix"

    def _lock_file(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def _choose_locking(self, mode: str) -> None:
        """Some SMB/NFS mounts refuse the POSIX file locks SQLite takes (every write then ends in « database is locked »
        although nothing else uses the file). On such a share SQLite's `unix-dotfile` VFS locks with a lock file instead,
        which any file system can do. Papyrus tries the normal locks first, on a share only, and falls back by itself.
        A lock file left by a crash is removed at start: there is only ever one Papyrus on a data folder."""
        if mode == "dotfile" or (mode == "auto" and self.network and self.journal == "delete" and not self._posix_locks_work()):
            self.vfs = "unix-dotfile"
            lock = self._lock_file()
            if lock.is_dir():
                shutil.rmtree(lock, ignore_errors=True)
            else:
                lock.unlink(missing_ok=True)
            print("papyrus: this share refuses file locks: SQLite locks with a lock file (unix-dotfile) instead", flush=True)

    def _posix_locks_work(self) -> bool:
        try:
            conn = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("CREATE TABLE IF NOT EXISTS _lock_probe(x)")
                conn.execute("DROP TABLE _lock_probe")
                conn.execute("COMMIT")
            finally:
                conn.close()
            return True
        except sqlite3.OperationalError as error:
            if "locked" in str(error) or "busy" in str(error):
                return False
            raise

    def _open(self) -> sqlite3.Connection:
        if self.vfs:
            conn = sqlite3.connect(f"file:{quote(str(self.path))}?vfs={self.vfs}", uri=True, timeout=30,
                                   isolation_level=None, check_same_thread=False)
        else:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if self.journal == "delete":
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        # small page cache: the idle footprint matters more than a few milliseconds
        conn.execute("PRAGMA cache_size=-4000")
        with self._lock:
            self._all.append(conn)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = self._open()
        return conn

    @contextmanager
    def connect(self):
        yield self.conn

    @contextmanager
    def tx(self):
        """A write transaction (BEGIN IMMEDIATE: the write lock is taken up front, no upgrade deadlock)."""
        conn = self.conn
        if conn.in_transaction:  # nested: the outer one commits
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (key, value))

    def backup_to(self, target: Path) -> None:
        """A consistent snapshot, even while the app writes (VACUUM INTO)."""
        if target.exists():
            target.unlink()
        self.conn.execute("VACUUM INTO ?", (str(target),))

    def close(self) -> None:
        with self._lock:
            for conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        self._local = threading.local()


def vec_to_blob(vec: np.ndarray | None) -> bytes | None:
    if vec is None:
        return None
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vec(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)
