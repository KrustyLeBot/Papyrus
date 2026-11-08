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


def _embed_model() -> str:
    from .embed import MODELS

    name = os.environ.get("EMBED_MODEL", "small").strip().lower() or "small"
    if name not in MODELS:
        raise SystemExit(f"papyrus: EMBED_MODEL={name!r} is unknown (choose {' or '.join(MODELS)})")
    return name


def _model_default() -> float:
    from .embed import MODELS

    return MODELS[_embed_model()]["semantic_min"]


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
    source_dir: Path = Path("/source")
    scan_interval: int = 60
    extensions: frozenset = frozenset({"pdf", "docx"})
    exclude: tuple = ("@eaDir", "#recycle", "#snapshot", "@Recycle", ".*", "~$*", "Thumbs.db", "desktop.ini", "*.tmp")
    max_file_mb: int = 300
    settle_seconds: int = 5
    ocr_threads: int = 2
    ocr_max_pages: int = 0
    ocr_langs: str = "fra+eng"
    embed_threads: int = 2
    embed_model: str = "small"
    embed_idle_unload_min: int = 15
    model_url: str = ""
    session_days: int = 30
    auth_disabled: bool = False
    trust_proxy: bool = False
    trusted_proxies: list = field(default_factory=list)
    cookie_secure: str = "auto"
    group_threshold: float = 0.88
    knn_threshold: float = 0.90
    backup_keep: int = 7
    build: str = "dev"
    search_defaults: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "papyrus.db"


    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def unlocked_dir(self) -> Path:
        return self.data_dir / "unlocked"

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
        for d in (self.data_dir, self.thumbs_dir, self.unlocked_dir, self.backups_dir):
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
            source_dir=Path(os.environ.get("PAPYRUS_SOURCE", "/source")),
            scan_interval=_int("SCAN_INTERVAL_SEC", 60, 10),
            extensions=frozenset(e.strip().lower().lstrip(".") for e in os.environ.get("SCAN_EXTENSIONS", "pdf,docx").split(",") if e.strip()),
            exclude=tuple(p.strip() for p in os.environ.get("SCAN_EXCLUDE", ",".join(cls.exclude)).split(",") if p.strip()),
            max_file_mb=_int("SCAN_MAX_FILE_MB", 300, 1),
            settle_seconds=_int("SCAN_SETTLE_SEC", 5, 0),
            ocr_threads=_int("OCR_THREADS", 2, 1, 16),
            ocr_max_pages=_int("OCR_MAX_PAGES", 0, 0),
            ocr_langs=os.environ.get("OCR_LANGS", "fra+eng").strip() or "fra+eng",
            embed_threads=_int("EMBED_THREADS", 2, 1, 16),
            embed_model=_embed_model(),
            embed_idle_unload_min=_int("EMBED_IDLE_UNLOAD_MIN", 15, 0),
            model_url=os.environ.get("MODEL_URL", "").strip(),
            session_days=_int("SESSION_DAYS", 30, 1, 365),
            auth_disabled=_bool("AUTH_DISABLED", False),
            trust_proxy=_bool("TRUST_PROXY", False),
            trusted_proxies=_networks(os.environ.get("TRUSTED_PROXIES", "")),
            cookie_secure=cookie,
            group_threshold=_float("GROUP_SIMILARITY", 0.88),
            knn_threshold=_float("KNN_SIMILARITY", 0.90),
            backup_keep=_int("BACKUP_KEEP", 7, 0),
            build=os.environ.get("PAPYRUS_BUILD", "dev"),
            search_defaults={"semantic_min": _float("SEARCH_SEMANTIC_MIN", _model_default()), "prefix": _bool("SEARCH_PREFIX", True),
                             "fuzzy": _bool("SEARCH_FUZZY", True)},
        )
