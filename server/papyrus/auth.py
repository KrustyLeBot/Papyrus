"""Login page sessions, Basic Auth for scripts, the login throttle and the CSRF check.

A session is an opaque random token in an HttpOnly cookie; only its SHA-256 is stored (sessions table), with the
user's password fingerprint, so changing a password or removing an account closes its sessions. Browser requests
that change something must carry the X-CSRF-Token header, equal to the readable papyrus_csrf cookie (double
submit); Basic Auth requests are not exposed to CSRF (browsers never send them on their own here)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from .config import Config
from .db import Database
from .htpasswd import Accounts

SESSION_COOKIE = "papyrus_session"
CSRF_COOKIE = "papyrus_csrf"
CSRF_HEADER = "x-csrf-token"
MAX_FAILURES = 5
WINDOW = 600


class Throttle:
    """At most 5 failed logins per client address in 10 minutes."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque] = defaultdict(deque)

    def _prune(self, ip: str, now: float) -> deque:
        q = self._failures[ip]
        while q and now - q[0] > WINDOW:
            q.popleft()
        return q

    def retry_after(self, ip: str) -> int:
        now = self.clock()
        with self._lock:
            q = self._prune(ip, now)
            if len(q) >= MAX_FAILURES:
                return int(WINDOW - (now - q[0])) + 1
            return 0

    def failed(self, ip: str) -> None:
        now = self.clock()
        with self._lock:
            self._prune(ip, now).append(now)
            if len(self._failures) > 10_000:
                for key in [k for k, q in self._failures.items() if not q or now - q[-1] > WINDOW]:
                    del self._failures[key]

    def succeeded(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


def _peer_trusted(request: Request, config: Config) -> bool:
    if not config.trust_proxy:
        return False
    if not config.trusted_proxies:
        return True
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    return any(peer in net for net in config.trusted_proxies)


def client_ip(request: Request, config: Config) -> str:
    if _peer_trusted(request, config):
        for header in ("cf-connecting-ip", "x-real-ip"):
            value = request.headers.get(header, "").strip()
            try:
                return str(ipaddress.ip_address(value))
            except ValueError:
                continue
    return request.client.host if request.client else "?"


def is_https(request: Request, config: Config) -> bool:
    if request.url.scheme == "https":
        return True
    return _peer_trusted(request, config) and request.headers.get("x-forwarded-proto", "").lower() == "https"


def cookie_secure(request: Request, config: Config) -> bool:
    if config.cookie_secure == "auto":
        return is_https(request, config)
    return config.cookie_secure == "true"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, config: Config, db: Database, accounts: Accounts | None):
        self.config = config
        self.db = db
        self.accounts = accounts
        self.throttle = Throttle()

    @property
    def disabled(self) -> bool:
        return self.config.auth_disabled

    # ---------------------------------------------------------------- sessions

    def open_session(self, user: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        fingerprint = self.accounts.fingerprint(user) if self.accounts else ""
        with self.db.tx():
            self.db.conn.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            self.db.conn.execute("INSERT INTO sessions(token_hash, user, created, expires) VALUES (?, ?, ?, ?)",
                                 (_hash(token), f"{user}\0{fingerprint}", now, now + self.config.session_days * 86400))
        return token

    def session_user(self, token: str | None) -> str | None:
        if not token or len(token) > 200:
            return None
        row = self.db.conn.execute("SELECT user, expires FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
        if row is None or row["expires"] < time.time():
            return None
        user, _, fingerprint = row["user"].partition("\0")
        if self.accounts is None or not hmac.compare_digest(fingerprint, self.accounts.fingerprint(user) or "-"):
            return None
        return user

    def close_session(self, token: str | None) -> None:
        if token:
            with self.db.tx():
                self.db.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))

    # ---------------------------------------------------------------- per request

    def basic_user(self, request: Request) -> tuple[str | None, bool]:
        """(user, header present). A wrong Basic login counts against the throttle like the login page."""
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return None, False
        ip = client_ip(request, self.config)
        if self.throttle.retry_after(ip):
            return None, True
        try:
            user, _, password = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return None, True
        if self.accounts and self.accounts.verify(user, password):
            self.throttle.succeeded(ip)
            return user, True
        self.throttle.failed(ip)
        print(f"papyrus: failed Basic Auth from {ip}", flush=True)
        return None, True

    def authenticate(self, request: Request) -> tuple[str | None, str]:
        """(user, how): how is "session", "basic", "disabled" or "" when not logged in."""
        if self.disabled:
            return "local", "disabled"
        user = self.session_user(request.cookies.get(SESSION_COOKIE))
        if user:
            return user, "session"
        user, present = self.basic_user(request)
        if user:
            return user, "basic"
        return None, "basic-failed" if present else ""

    @staticmethod
    def csrf_ok(request: Request) -> bool:
        cookie = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get(CSRF_HEADER, "")
        return bool(cookie) and hmac.compare_digest(cookie, header)
