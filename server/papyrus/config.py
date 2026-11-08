"""Settings, read once from the environment (see .env.example)."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int, low: int | None = None, high: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        raise SystemExit(f"papyrus: {name}={raw!r} is not a number") from None
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        raise SystemExit(f"papyrus: {name}={raw!r} is not a number") from None


def _networks(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                raise SystemExit(f"papyrus: TRUSTED_PROXIES: {part!r} is not an address or a network") from None
    return nets


@dataclass
class Config:
    data_dir: Path
    conf_dir: Path
    model_dir: Path
    static_dir: Path
    port: int = 8080
    ocr_threads: int = 2
    ocr_max_pages: int = 0
    ocr_langs: str = "fra+eng"
    embed_threads: int = 2
    embed_idle_unload_min: int = 15
    model_url: str = ""
    max_upload_mb: int = 200
    session_days: int = 30
    auth_disabled: bool = False
    trust_proxy: bool = False
    trusted_proxies: list = field(default_factory=list)
    cookie_secure: str = "auto"
    group_threshold: float = 0.88
    knn_threshold: float = 0.90
    backup_keep: int = 7
    build: str = "dev"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "papyrus.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def locked_dir(self) -> Path:
        return self.data_dir / "locked"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def htpasswd_path(self) -> Path:
        return self.conf_dir / "httpasswd"

    @property
    def rules_path(self) -> Path:
        return self.conf_dir / "rules.yaml"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.files_dir, self.thumbs_dir, self.locked_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Config":
        data = Path(os.environ.get("PAPYRUS_DATA", "/data"))
        here = Path(__file__).resolve().parent
        cookie = os.environ.get("COOKIE_SECURE", "auto").strip().lower() or "auto"
        if cookie not in ("auto", "true", "false"):
            raise SystemExit("papyrus: COOKIE_SECURE must be auto, true or false")
        return cls(
            data_dir=data,
            conf_dir=Path(os.environ.get("PAPYRUS_CONF", "/conf")),
            # a model baked into the image wins; otherwise it is downloaded into the data folder
            model_dir=Path(os.environ.get("PAPYRUS_MODEL_DIR", "") or (data / "models")),
            static_dir=Path(os.environ.get("PAPYRUS_STATIC", "") or (here.parent / "static")),
            port=_int("PAPYRUS_PORT", 8080),
            ocr_threads=_int("OCR_THREADS", 2, 1, 16),
            ocr_max_pages=_int("OCR_MAX_PAGES", 0, 0),
            ocr_langs=os.environ.get("OCR_LANGS", "fra+eng").strip() or "fra+eng",
            embed_threads=_int("EMBED_THREADS", 2, 1, 16),
            embed_idle_unload_min=_int("EMBED_IDLE_UNLOAD_MIN", 15, 0),
            model_url=os.environ.get("MODEL_URL", "").strip(),
            max_upload_mb=_int("MAX_UPLOAD_MB", 200, 1),
            session_days=_int("SESSION_DAYS", 30, 1, 365),
            auth_disabled=_bool("AUTH_DISABLED", False),
            trust_proxy=_bool("TRUST_PROXY", False),
            trusted_proxies=_networks(os.environ.get("TRUSTED_PROXIES", "")),
            cookie_secure=cookie,
            group_threshold=_float("GROUP_SIMILARITY", 0.88),
            knn_threshold=_float("KNN_SIMILARITY", 0.90),
            backup_keep=_int("BACKUP_KEEP", 7, 0),
            build=os.environ.get("PAPYRUS_BUILD", "dev"),
        )
