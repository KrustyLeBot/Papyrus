"""SQLite storage: one file in the data folder, WAL mode, FTS5 for the full-text index.

One connection per thread (the API's thread pool, the worker): SQLite serialises the writers itself, and
busy_timeout makes a writer wait instead of failing while another one commits."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    uuid            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    title_manual    INTEGER NOT NULL DEFAULT 0,
    original_name   TEXT NOT NULL,
    ext             TEXT NOT NULL,
    mime            TEXT NOT NULL DEFAULT '',
    size            INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT NOT NULL,
    sha256_encrypted TEXT,
    path            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    error           TEXT,
    text            TEXT NOT NULL DEFAULT '',
    pages           INTEGER NOT NULL DEFAULT 0,
    ocr             INTEGER NOT NULL DEFAULT 0,
    doc_date        TEXT,
    date_source     TEXT NOT NULL DEFAULT 'upload',
    types           TEXT NOT NULL DEFAULT '[]',
    locked          INTEGER NOT NULL DEFAULT 0,
    unlocked_at     TEXT,
    suggestions     TEXT NOT NULL DEFAULT '[]',
    suggestion_applied INTEGER NOT NULL DEFAULT 0,
    embedding       BLOB,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    processed_at    TEXT
);
CREATE INDEX IF NOT EXISTS documents_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS documents_sha_enc ON documents(sha256_encrypted);
CREATE INDEX IF NOT EXISTS documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS documents_date ON documents(doc_date);

CREATE TABLE IF NOT EXISTS doc_keys (
    doc_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    label   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (doc_id, key, value)
);
CREATE INDEX IF NOT EXISTS doc_keys_kv ON doc_keys(key, value);

CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT,
    type_name   TEXT,
    is_inbox    INTEGER NOT NULL DEFAULT 0,
    auto        INTEGER NOT NULL DEFAULT 0,
    centroid    BLOB,
    centroid_n  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS groups_parent ON groups(parent_id);

CREATE TABLE IF NOT EXISTS group_keys (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    label    TEXT NOT NULL DEFAULT '',
    source   TEXT NOT NULL DEFAULT 'auto',
    PRIMARY KEY (group_id, key, value)
);
CREATE INDEX IF NOT EXISTS group_keys_kv ON group_keys(key, value);

CREATE TABLE IF NOT EXISTS doc_groups (
    doc_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    source   TEXT NOT NULL DEFAULT 'auto',
    PRIMARY KEY (doc_id, group_id)
);
CREATE INDEX IF NOT EXISTS doc_groups_group ON doc_groups(group_id);

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
    title, body, filename, tags,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
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
