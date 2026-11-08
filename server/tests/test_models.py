import pytest

from papyrus import embed
from papyrus.embed import MODELS, Embedder
from papyrus.services import Services

from .conftest import make_config


def put_model(base, name, *, direct=False):
    spec = MODELS[name]
    folder = base if direct else base / spec["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    for file in spec["files"]:
        (folder / file).write_bytes(b"x")
    return folder


def test_each_model_has_its_folder_dimension_and_default_threshold(tmp_path):
    put_model(tmp_path, "small")
    put_model(tmp_path, "base")
    small, base = Embedder([tmp_path], idle_minutes=0, model="small"), Embedder([tmp_path], idle_minutes=0, model="base")
    assert (small.dim, base.dim) == (384, 768)
    assert small.model_dir().name == "e5-small" and base.model_dir().name == "e5-base"
    assert MODELS["small"]["semantic_min"] == 0.86 and MODELS["base"]["semantic_min"] == 0.84
    assert all(len(sha) == 64 for spec in MODELS.values() for _remote, sha in spec["files"].values())  # pinned, checked


def test_the_first_layout_of_the_small_model_still_works_and_base_never_borrows_it(tmp_path):
    put_model(tmp_path, "small", direct=True)  # files straight in models/, as the first versions put them
    assert Embedder([tmp_path], idle_minutes=0, model="small").available
    assert not Embedder([tmp_path], idle_minutes=0, model="base").available


def test_unknown_model_is_refused_clearly(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="EMBED_MODEL"):
        Embedder([tmp_path], idle_minutes=0, model="huge")
    monkeypatch.setenv("EMBED_MODEL", "huge")
    from papyrus.config import Config

    with pytest.raises(SystemExit, match="EMBED_MODEL"):
        Config.from_env()


def test_changing_the_model_clears_the_embeddings_and_back(tmp_path):
    cfg = make_config(tmp_path, auth_disabled=True)
    svc = Services(cfg)
    svc.embedder.model_dirs = [tmp_path / "none"]
    svc.db.conn.execute("INSERT INTO documents(uuid, title, original_name, ext, sha256, created_at, updated_at, embedding, status) "
                        "VALUES ('u', 't', 'a.pdf', 'pdf', 'abc', 'x', 'x', ?, 'done')", (b"\x00" * 384 * 4,))
    assert svc.db.conn.execute("SELECT embedding FROM documents").fetchone()[0] is not None
    svc.close()

    svc2 = Services(make_config(tmp_path, auth_disabled=True, embed_model="base"))  # another model: other size, other scores
    assert svc2.embedder.name == "base" and svc2.db.conn.execute("SELECT embedding FROM documents").fetchone()[0] is None
    assert svc2.db.setting("embed_model") == "base"
    svc2.close()

    svc3 = Services(make_config(tmp_path, auth_disabled=True, embed_model="base"))  # same model: nothing is touched
    svc3.db.conn.execute("UPDATE documents SET embedding = ?", (b"\x00" * 768 * 4,))
    svc3.close()
    svc4 = Services(make_config(tmp_path, auth_disabled=True, embed_model="base"))
    assert svc4.db.conn.execute("SELECT embedding FROM documents").fetchone()[0] is not None
    svc4.close()


def test_vectors_of_another_size_are_skipped_not_fatal(client, services, tmp_path, no_model):
    services.db.conn.execute("INSERT INTO documents(uuid, title, original_name, ext, sha256, created_at, updated_at, embedding, status) "
                             "VALUES ('u', 't', 'a.pdf', 'pdf', 'abc', 'x', 'x', ?, 'done')", (b"\x00" * 768 * 4,))
    ids, matrix = services.vectors.get()
    assert len(ids) == 0 and matrix.shape == (0, 384)
    assert services.searcher.similar(1) == []


def test_base_model_is_selectable_by_the_environment(monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "base")
    from papyrus.config import Config

    cfg = Config.from_env()
    assert cfg.embed_model == "base" and cfg.search_defaults["semantic_min"] == 0.84
    monkeypatch.setenv("SEARCH_SEMANTIC_MIN", "0.9")
    assert Config.from_env().search_defaults["semantic_min"] == 0.9
