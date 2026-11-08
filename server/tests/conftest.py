import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from papyrus.app import create_app
from papyrus.config import Config
from papyrus.htpasswd import hash_password
from papyrus.services import Services

MODEL_DIR = os.environ.get("PAPYRUS_TEST_MODEL", "")
EXTENSIONS = frozenset({"pdf", "docx", "txt", "png", "jpg"})  # the tests also scan text files and scans


def make_config(tmp_path: Path, **over) -> Config:
    conf = tmp_path / "conf"
    conf.mkdir(exist_ok=True)
    (conf / "httpasswd").write_text(f"jerome:{hash_password('correct horse battery')}\n")
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    cfg = Config(data_dir=tmp_path / "data", conf_dir=conf, model_dir=tmp_path / "models", static_dir=tmp_path / "static",
                 source_dir=source, extensions=EXTENSIONS, settle_seconds=0, embed_idle_unload_min=0, **over)
    if MODEL_DIR:
        cfg.model_dir = Path(MODEL_DIR)
    return cfg


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path, auth_disabled=True)


@pytest.fixture
def services(cfg):
    svc = Services(cfg)
    yield svc
    svc.close()


@pytest.fixture
def client(cfg, services):
    app = create_app(cfg, services, start_worker=False)
    with TestClient(app) as c:
        c.headers["X-CSRF-Token"] = "t"
        c.cookies.set("papyrus_csrf", "t")
        yield c


@pytest.fixture
def vault(services):
    """The key of the (auth-disabled) account in memory, as after a login: saved PDF passwords can be kept and used."""
    services.vault.unlock("local", "the account password")
    return services.vault


@pytest.fixture
def no_model(cfg, services):
    """Forces degraded mode (rules, keys and full-text only): deterministic whatever the machine holds."""
    services.embedder.model_dirs = [cfg.data_dir / "no-model"]
    return services


def drain(services, limit=200):
    for _ in range(limit):
        if not services.worker.run_one():
            return
    raise AssertionError("worker never finished")


def place(services, src: Path, rel: str | None = None, mtime: float | None = None) -> Path:
    """Copies a file into the source folder (where the user keeps documents)."""
    dst = services.config.source_dir / (rel or src.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)
    if mtime is not None:
        os.utime(dst, (mtime, mtime))
    return dst


def upload(client, path: Path, name: str | None = None):
    """The test-suite's way to « add a document »: put the file in the source folder, scan, analyse. Returns what
    the old upload answered: status queued | duplicate | password_required | rejected, and the document id."""
    services = client.app.state.services
    cfg = services.config
    db = services.db
    from papyrus.scanner import sha256_file

    sha = sha256_file(path)
    existed = db.conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha,)).fetchone()
    rel = name or path.name
    place(services, path, rel)
    services.scanner.scan()
    drain(services)
    row = db.conn.execute("SELECT id, status FROM documents WHERE sha256 = ?", (sha,)).fetchone()
    if row is None:
        return {"status": "rejected", "id": None, "name": rel}
    status = "password_required" if row["status"] == "password_required" else "duplicate" if existed else "queued"
    return {"status": status, "id": row["id"], "name": rel}


requires_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
