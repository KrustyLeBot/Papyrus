import base64
import time

import pytest
from fastapi.testclient import TestClient

from papyrus import htpasswd
from papyrus.app import create_app
from papyrus.services import Services

from . import docgen
from .conftest import drain, make_config, upload


@pytest.fixture
def secured(tmp_path):
    cfg = make_config(tmp_path)
    svc = Services(cfg)
    app = create_app(cfg, svc, start_worker=False)
    with TestClient(app, base_url="http://testserver") as c:
        yield c, cfg, svc
    svc.close()


def test_login_page_flow_cookies_and_csrf(secured):
    c, cfg, _svc = secured
    assert c.get("/api/documents").status_code == 401
    assert c.get("/health").status_code == 200
    bad = c.post("/api/auth/login", json={"user": "jerome", "password": "nope"})
    assert bad.status_code == 401
    ok = c.post("/api/auth/login", json={"user": "jerome", "password": "correct horse battery"})
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/api/auth/me").json()["user"] == "jerome"
    # a change without the CSRF header is refused, with it accepted
    assert c.post("/api/groups", json={"name": "X"}).status_code == 403
    csrf = c.cookies.get("papyrus_csrf")
    assert c.post("/api/groups", json={"name": "X"}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/documents").status_code == 401


def test_secure_cookie_over_https_behind_proxy(tmp_path):
    cfg = make_config(tmp_path, trust_proxy=True)
    svc = Services(cfg)
    app = create_app(cfg, svc, start_worker=False)
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"user": "jerome", "password": "correct horse battery"},
                   headers={"X-Forwarded-Proto": "https"})
        assert "secure" in r.headers["set-cookie"].lower()
    svc.close()


def test_basic_auth_for_scripts(secured):
    c, _cfg, _svc = secured
    token = base64.b64encode(b"jerome:correct horse battery").decode()
    assert c.get("/api/documents", headers={"Authorization": f"Basic {token}"}).status_code == 200
    bad = base64.b64encode(b"jerome:wrong").decode()
    assert c.get("/api/documents", headers={"Authorization": f"Basic {bad}"}).status_code == 401


def test_login_throttle_five_failures(secured):
    c, _cfg, _svc = secured
    for _ in range(5):
        assert c.post("/api/auth/login", json={"user": "jerome", "password": "x"}).status_code == 401
    r = c.post("/api/auth/login", json={"user": "jerome", "password": "correct horse battery"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_weak_hash_formats_refused_with_a_clear_message(tmp_path, capfd):
    users, problems = htpasswd.parse("a:$apr1$abc$xyz\nb:{SHA}W6ph5Mm5Pz8GgiULbPgzG37mj9g=\nc:plaintext\n"
                                     f"d:{htpasswd.hash_password('x')}\n")
    assert list(users) == ["d"]
    assert len(problems) == 3 and "bcrypt" in problems[0] and "MD5" in problems[0]


def test_password_change_closes_sessions(secured):
    c, cfg, svc = secured
    c.post("/api/auth/login", json={"user": "jerome", "password": "correct horse battery"})
    assert c.get("/api/auth/me").status_code == 200
    time.sleep(0.05)
    cfg.htpasswd_path.write_text(f"jerome:{htpasswd.hash_password('another password')}\n")
    import os
    os.utime(cfg.htpasswd_path, (time.time() + 5, time.time() + 5))
    assert c.get("/api/auth/me").status_code == 401


def test_upload_limits_and_unknown_formats(client, cfg, tmp_path, no_model):
    cfg.max_upload_mb = 1
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (1024 * 1024 + 10))
    assert upload(client, big)["status"] == "rejected"
    exe = tmp_path / "x.bin"
    exe.write_bytes(b"MZ\x90\x00" + b"\x00" * 100)
    assert upload(client, exe)["status"] == "rejected"
    txt = tmp_path / "note.txt"
    txt.write_text("Facture n° 12\nTotal TTC 120,00", encoding="utf-8")
    assert upload(client, txt)["status"] == "queued"
    assert upload(client, txt)["status"] == "duplicate"


def test_files_are_stored_with_opaque_names_and_never_modified(client, cfg, tmp_path, no_model):
    src = docgen.pdf(tmp_path / "Mon relevé très privé.pdf", ["Relevé de compte", "solde"])
    original = src.read_bytes()
    r = upload(client, src)
    stored = next(cfg.files_dir.rglob("*.pdf"))
    assert stored.read_bytes() == original
    assert "relev" not in stored.name.lower() and stored.parent.parent.name.isdigit()
    drain(client.app.state.services)
    doc = client.get(f"/api/documents/{r['id']}").json()
    assert doc["original_name"] == "Mon relevé très privé.pdf"
    assert client.get(f"/api/documents/{r['id']}/file").content == original


def test_search_accents_prefix_phrase_and_filters(client, services, tmp_path, no_model):
    a = tmp_path / "a.txt"
    a.write_text("Facture d'électricité du mois de mars\nTotal TTC 120,00", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("Contrat de travail signé le 02/01/2026\nPériode d'essai", encoding="utf-8")
    upload(client, a)
    upload(client, b)
    drain(services)
    titles = lambda q, **kw: [d["title"] for d in client.get("/api/search", params={"q": q, **kw}).json()["items"]]
    assert titles("electricite") == ["A"]              # accents ignored
    assert titles("fact*") == ["A"]                    # prefix
    assert titles('"periode d\'essai"') == ["B"]       # phrase
    assert titles("facture mars") == ["A"]             # every word required
    assert titles("facture contrat") == []
    assert titles("electricite", exact="1") == ["A"]
    r = client.get("/api/search", params={"q": "electricite"}).json()["items"][0]
    assert "<mark>" in r["snippet"] and "<script" not in r["snippet"]
    assert titles("travail", date_from="2027-01-01") == []
    assert titles("travail", type="Emploi") == ["B"]


def test_search_snippet_escapes_html(client, services, tmp_path, no_model):
    f = tmp_path / "x.txt"
    f.write_text("<script>alert(1)</script> facture", encoding="utf-8")
    upload(client, f)
    drain(services)
    snippet = client.get("/api/search", params={"q": "facture"}).json()["items"][0]["snippet"]
    assert "<script>" not in snippet and "&lt;script&gt;" in snippet


def test_tags_manual_never_removed_by_automatic(client, services, tmp_path, no_model):
    f = tmp_path / "n.txt"
    f.write_text("Facture n° 5\nMontant HT 10,00\nTotal TTC 12,00\nDate de facture : 01/02/2026", encoding="utf-8")
    doc_id = upload(client, f)["id"]
    drain(services)
    client.post(f"/api/documents/{doc_id}/tags", json={"name": "à garder"})
    client.post("/api/groups/reclassify", json={"group_id": None})
    client.post(f"/api/documents/{doc_id}/reprocess")
    drain(services)
    names = {t["name"] for t in client.get(f"/api/documents/{doc_id}").json()["tags"]}
    assert "à garder" in names and "2026" in names and "facture" in names


def test_delete_document_removes_files_and_index(client, services, cfg, tmp_path, no_model):
    f = tmp_path / "z.txt"
    f.write_text("Facture jetable", encoding="utf-8")
    doc_id = upload(client, f)["id"]
    drain(services)
    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert not list(cfg.files_dir.rglob("*.txt")) and not list(cfg.thumbs_dir.glob("*"))
    assert client.get("/api/search", params={"q": "jetable"}).json()["items"] == []


def test_degraded_mode_reports_absent_model(client, no_model):
    s = client.get("/api/admin/status").json()
    assert s["model"]["status"] == "absent"
    assert client.get("/api/search", params={"q": "x"}).json()["semantic"] is False


def test_incompatible_cpu_turns_the_model_off_instead_of_crashing(client, services, tmp_path, monkeypatch):
    import subprocess

    from papyrus import embed

    model = tmp_path / "m"
    model.mkdir()
    for name in embed.FILES:
        (model / name).write_bytes(b"x")
    services.embedder.model_dirs = [model]
    assert services.embedder.available
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, -4, b"", b""))
    f = tmp_path / "t.txt"
    f.write_text("Facture n° 1\nTotal TTC 12,00", encoding="utf-8")
    upload(client, f)
    drain(services)  # the document is processed anyway, without an embedding
    assert client.get("/api/documents").json()["items"][0]["status"] == "done"
    assert not services.embedder.available and "processeur" in services.embedder.error
    status = client.get("/api/admin/status").json()["model"]
    assert status["status"] == "absent" and "processeur" in status["error"]
