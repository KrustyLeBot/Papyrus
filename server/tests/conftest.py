import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from papyrus.app import create_app
from papyrus.config import Config
from papyrus.htpasswd import hash_password
from papyrus.services import Services

MODEL_DIR = os.environ.get("PAPYRUS_TEST_MODEL", "")


def make_config(tmp_path: Path, **over) -> Config:
    conf = tmp_path / "conf"
    conf.mkdir(exist_ok=True)
    (conf / "httpasswd").write_text(f"jerome:{hash_password('correct horse battery')}\n")
    cfg = Config(data_dir=tmp_path / "data", conf_dir=conf, model_dir=tmp_path / "models", static_dir=tmp_path / "static",
                 embed_idle_unload_min=0, **over)
    if MODEL_DIR and over.pop("with_model", True):
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
def no_model(cfg, services):
    """Forces degraded mode (rules, keys and full-text only): deterministic whatever the machine holds."""
    services.embedder.model_dirs = [cfg.data_dir / "no-model"]
    return services


def drain(services, limit=100):
    for _ in range(limit):
        if not services.worker.run_one():
            return
    raise AssertionError("worker never finished")


def upload(client, path: Path, name: str | None = None):
    with path.open("rb") as f:
        r = client.post("/api/upload", files=[("files", (name or path.name, f))])
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


requires_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
