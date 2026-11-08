import base64
import time

import pytest
from fastapi.testclient import TestClient

from papyrus import htpasswd
from papyrus.app import create_app
from papyrus.services import Services

from . import docgen
from .conftest import drain, make_config, place, upload


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
    assert c.post("/api/admin/reload-rules").status_code == 403
    csrf = c.cookies.get("papyrus_csrf")
    assert c.post("/api/admin/reload-rules", headers={"X-CSRF-Token": csrf}).status_code == 200
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


def test_search_accents_prefix_phrase_and_filters(client, services, tmp_path, no_model):
    a = tmp_path / "a.txt"
    a.write_text("Facture d'électricité du mois de mars\nTotal TTC 120,00", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("Contrat de travail signé le 02/01/2026\nPériode d'essai", encoding="utf-8")
    upload(client, a)
    upload(client, b)
    drain(services)
    titles = lambda q, **kw: [d["original_name"][0].upper() for d in client.get("/api/search", params={"q": q, **kw}).json()["items"]]
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


def test_content_tags_from_keywords_without_any_type(client, services, tmp_path, no_model):
    f = tmp_path / "notice.txt"
    f.write_text("QUICK START GUIDE\nEpomaker keyboard\nWarranty: 12 months.\nPress FN + Q for Android.", encoding="utf-8")
    doc_id = upload(client, f)["id"]
    drain(services)
    doc = client.get(f"/api/documents/{doc_id}").json()
    names = {t["name"] for t in doc["tags"]}
    assert {"notice", "garantie"} <= names
    assert [d["id"] for d in client.get("/api/search", params={"q": "notice", "tag": "notice"}).json()["items"]] == [doc_id]


def test_diploma_is_typed_and_tagged(client, services, tmp_path, no_model):
    f = tmp_path / "scan.txt"
    f.write_text("RÉPUBLIQUE FRANÇAISE\nMINISTÈRE DE L'ÉDUCATION NATIONALE\nACADÉMIE DE GRENOBLE\nDIPLÔME DU BACCALAURÉAT GÉNÉRAL\n"
                 "Fait à Grenoble, le 22 Septembre 2015", encoding="utf-8")
    doc = client.get(f"/api/documents/{upload(client, f)['id']}").json()
    drain(services)
    doc = client.get(f"/api/documents/{doc['id']}").json()
    assert doc["types"] == ["Études"]
    assert {"diplôme", "2015"} <= {t["name"] for t in doc["tags"]}


def test_reprocess_all_keeps_what_was_set_by_hand(client, services, tmp_path, no_model):
    f = tmp_path / "scan0001.txt"
    f.write_text("ATTESTATION DE STAGE\nNous attestons que le stagiaire a effectue un stage de six semaines.", encoding="utf-8")
    doc_id = upload(client, f)["id"]
    drain(services)
    client.post(f"/api/documents/{doc_id}/tags", json={"name": "perso"})
    client.patch(f"/api/documents/{doc_id}", json={"doc_date": "2020-01-02"})
    assert client.post("/api/admin/reprocess-all").json()["documents"] == 1
    assert client.get("/api/queue").json()["items"][0]["status"] == "pending"
    drain(services)
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["status"] == "done" and doc["date"] == "2020-01-02" and "perso" in {t["name"] for t in doc["tags"]}


def test_a_document_is_read_from_the_source_folder_and_never_modified(client, services, cfg, tmp_path, no_model):
    src = docgen.pdf(tmp_path / "Mon relevé très privé.pdf", ["Relevé de compte", "solde"])
    original = src.read_bytes()
    r = upload(client, src, name="Banque/2026/Mon relevé très privé.pdf")
    stored = cfg.source_dir / "Banque" / "2026" / "Mon relevé très privé.pdf"
    assert stored.read_bytes() == original
    doc = client.get(f"/api/documents/{r['id']}").json()
    assert doc["path"] == "Banque/2026/Mon relevé très privé.pdf" and doc["folder"] == "Banque/2026"
    assert client.get(f"/api/documents/{r['id']}/file").content == original  # served from where it is
    assert not list(cfg.data_dir.glob("files")) and not list(cfg.data_dir.glob("**/Mon*"))  # no copy kept


def test_forget_removes_the_analysis_not_the_file_and_it_stays_out(client, services, cfg, tmp_path, no_model):
    f = tmp_path / "z.txt"
    f.write_text("Facture jetable", encoding="utf-8")
    doc_id = upload(client, f)["id"]
    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert (cfg.source_dir / "z.txt").exists()  # the file is untouched
    assert not list(cfg.thumbs_dir.glob("*"))
    assert client.get("/api/search", params={"q": "jetable"}).json()["items"] == []
    services.scanner.scan()  # a later scan does not bring it back
    assert client.get("/api/documents").json()["items"] == []
    assert client.get("/api/admin/status").json()["ignored"] == 1
    assert client.post("/api/admin/unignore-all").json()["documents"] == 1  # bring it back on purpose
    services.scanner.scan()
    assert client.get("/api/documents").json()["total"] == 1


def test_search_completes_the_last_word_and_forgives_typos(client, services, tmp_path, no_model):
    f = tmp_path / "paie.txt"
    f.write_text("Bulletin de salaire de septembre\nSalaire brut 4 000\nCotisations salariales", encoding="utf-8")
    other = tmp_path / "autre.txt"
    other.write_text("Facture d'électricité du mois de mars", encoding="utf-8")
    upload(client, f)
    upload(client, other)
    found = lambda q, **kw: [d["original_name"] for d in client.get("/api/search", params={"q": q, **kw}).json()["items"]]
    assert found("salai") == ["paie.txt"]                    # typing: the last word is completed
    assert found("bulletin salai") == ["paie.txt"]
    assert found("salare") == ["paie.txt"]                   # one typo
    assert found("electricte") == ["autre.txt"]              # accents and a typo
    assert found("salai", exact="1") == []                   # « exact » means exact
    data = client.get("/api/search", params={"q": "salare"}).json()
    assert data["corrections"] == {"salare": ["salaire", "salaires"][:len(data["corrections"]["salare"])]} or "salaire" in data["corrections"]["salare"]
    # the settings can switch both off
    client.put("/api/admin/search-settings", json={"semantic_min": 0.86, "prefix": False, "fuzzy": False})
    assert found("salai") == [] and found("salare") == []
    assert found("salaire") == ["paie.txt"]
    assert client.get("/api/admin/status").json()["search"] == {"semantic_min": 0.86, "prefix": False, "fuzzy": False}


def test_search_settings_are_validated(client):
    assert client.put("/api/admin/search-settings", json={"semantic_min": 0.2, "prefix": True, "fuzzy": True}).status_code == 422
    r = client.put("/api/admin/search-settings", json={"semantic_min": 0.9, "prefix": True, "fuzzy": True})
    assert r.status_code == 200 and r.json()["semantic_min"] == 0.9


def test_folder_filter_and_listing(client, services, tmp_path, no_model):
    a = tmp_path / "a.txt"
    a.write_text("Facture A n° 1 total TTC", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("Facture B n° 2 total TTC", encoding="utf-8")
    c = tmp_path / "c.txt"
    c.write_text("Facture C n° 3 total TTC", encoding="utf-8")
    upload(client, a, name="Impots/2025/a.txt")
    upload(client, b, name="Impots/2026/b.txt")
    upload(client, c, name="Bordel/c.txt")
    folders = {f["path"]: f["count"] for f in client.get("/api/folders").json()["items"]}
    assert folders == {"Impots/2025": 1, "Impots/2026": 1, "Bordel": 1}
    names = lambda **kw: sorted(d["original_name"] for d in client.get("/api/documents", params=kw).json()["items"])
    assert names(folder="Impots") == ["a.txt", "b.txt"]
    assert names(folder="Impots", children="0") == []
    assert names(folder="Impots/2026") == ["b.txt"]
    assert names(folder="Imp") == []  # a folder name is not a prefix match
    hits = client.get("/api/search", params={"q": "facture", "folder": "Impots"}).json()["items"]
    assert sorted(d["original_name"] for d in hits) == ["a.txt", "b.txt"]
    assert [d["original_name"] for d in client.get("/api/search", params={"q": "bordel"}).json()["items"]] == ["c.txt"]  # folder names are searchable


def test_hide_keeps_everything_but_leaves_lists_and_search(client, services, cfg, tmp_path, no_model):
    f = tmp_path / "secret.txt"
    f.write_text("Facture confidentielle n° 7\nTotal TTC 120,00", encoding="utf-8")
    g = tmp_path / "autre.txt"
    g.write_text("Facture ordinaire n° 8\nTotal TTC 80,00", encoding="utf-8")
    doc_id = upload(client, f, name="Perso/secret.txt")["id"]
    upload(client, g, name="Perso/autre.txt")
    client.post(f"/api/documents/{doc_id}/tags", json={"name": "garde-moi"})
    names = lambda **kw: sorted(d["original_name"] for d in client.get("/api/documents", params=kw).json()["items"])
    found = lambda q: sorted(d["original_name"] for d in client.get("/api/search", params={"q": q}).json()["items"])
    assert found("facture") == ["autre.txt", "secret.txt"]

    r = client.patch(f"/api/documents/{doc_id}", json={"hidden": True})
    assert r.status_code == 200 and r.json()["hidden"] is True
    assert names() == ["autre.txt"] and found("facture") == ["autre.txt"] and found("confidentielle") == []
    assert {f["path"]: f["count"] for f in client.get("/api/folders").json()["items"]} == {"Perso": 1}
    assert names(hidden="1") == ["secret.txt"]                              # the « Masqués » category
    assert client.get("/api/queue").json()["hidden"] == 1
    assert client.get("/api/admin/status").json()["by_status"] == {"done": 2}  # still analysed, nothing was deleted

    # the file moves and the analysis is redone: it stays hidden, and stays out of the search
    (cfg.source_dir / "Perso" / "secret.txt").rename(cfg.source_dir / "Perso" / "renommé.txt")
    services.scanner.scan()
    client.post(f"/api/documents/{doc_id}/reprocess")
    drain(services)
    assert names(hidden="1") == ["renommé.txt"] and found("confidentielle") == []

    client.patch(f"/api/documents/{doc_id}", json={"hidden": False})       # shown again, with its tags
    assert names() == ["autre.txt", "renommé.txt"] and found("confidentielle") == ["renommé.txt"]
    assert "garde-moi" in {t["name"] for t in client.get(f"/api/documents/{doc_id}").json()["tags"]}
    assert names(hidden="1") == [] and client.get("/api/queue").json()["hidden"] == 0


def test_editing_the_title_and_date_from_papyrus(client, services, tmp_path, no_model):
    f = tmp_path / "scan0001.txt"
    f.write_text("Facture n° 5\nTotal TTC 12,00", encoding="utf-8")
    doc_id = upload(client, f)["id"]
    r = client.patch(f"/api/documents/{doc_id}", json={"title": "  Ma   facture de plomberie  ", "doc_date": "2025-03-04"})
    assert r.status_code == 200
    doc = r.json()
    assert doc["title"] == "Ma facture de plomberie" and doc["title_manual"] and doc["date"] == "2025-03-04" and doc["date_source"] == "manual"
    assert [d["id"] for d in client.get("/api/search", params={"q": "plomberie"}).json()["items"]] == [doc_id]  # indexed under the new title
    assert client.patch(f"/api/documents/{doc_id}", json={"doc_date": "pas une date"}).status_code == 422


def test_the_key_comes_from_the_account_password(tmp_path, no_model_dir=None):
    """Saved PDF passwords are encrypted with a key derived from the login password: kept in memory after a login,
    gone from memory after a restart (until the next login), unreadable after a password change."""
    import pikepdf

    cfg = make_config(tmp_path)
    svc = Services(cfg)
    svc.embedder.model_dirs = [tmp_path / "none"]
    app = create_app(cfg, svc, start_worker=False)
    protected = docgen.pdf(tmp_path / "p.pdf", ["BULLETIN DE SALAIRE", "Net à payer 1 000"], user_password="pdf-secret-pw")
    with TestClient(app) as c:
        assert svc.vault.status() == {"saved": 0, "locked": False, "unlocked_accounts": 0}
        c.post("/api/auth/login", json={"user": "jerome", "password": "correct horse battery"})
        csrf = {"X-CSRF-Token": c.cookies.get("papyrus_csrf")}
        assert svc.vault.is_unlocked("jerome")
        place(svc, protected)
        svc.scanner.scan()
        drain(svc)
        doc_id = c.get("/api/locked").json()["items"][0]["id"]
        r = c.post(f"/api/documents/{doc_id}/unlock", json={"password": "pdf-secret-pw"}, headers=csrf).json()
        assert r["remembered"] is True
    svc.close()

    # a restart: the session cookie is still valid, but the key is not in memory
    svc2 = Services(cfg)
    svc2.embedder.model_dirs = [tmp_path / "none"]
    app2 = create_app(cfg, svc2, start_worker=False)
    with TestClient(app2) as c2:
        c2.cookies.set("papyrus_session", c.cookies.get("papyrus_session"))
        assert c2.get("/api/auth/me").status_code == 200
        assert svc2.vault.status()["locked"] is True and svc2.vault.candidates("x") == []
        assert c2.get("/api/queue").json()["vault_locked"] is True
        # a new protected PDF with the same password waits: nobody has logged in since the restart
        place(svc2, docgen.pdf(tmp_path / "q.pdf", ["autre"], user_password="pdf-secret-pw"), "q.pdf")
        svc2.scanner.scan()
        drain(svc2)
        assert len(c2.get("/api/locked").json()["items"]) == 1
        # logging in brings the key back, and the waiting PDF opens by itself on the next analysis
        c2.post("/api/auth/login", json={"user": "jerome", "password": "correct horse battery"})
        assert svc2.vault.status()["locked"] is False
        drain(svc2)  # the login put the waiting PDFs back in the queue
        assert c2.get("/api/locked").json()["items"] == []
    svc2.close()

    # the account password changes: what was saved cannot be read any more and is dropped at the next login
    cfg.htpasswd_path.write_text(f"jerome:{htpasswd.hash_password('a brand new password')}\n")
    import os
    os.utime(cfg.htpasswd_path, (time.time() + 9, time.time() + 9))
    svc3 = Services(cfg)
    assert svc3.vault.count() >= 1
    assert svc3.vault.unlock("jerome", "a brand new password") >= 1
    assert svc3.vault.count() == 0 and svc3.vault.candidates("x") == []
    svc3.close()


def test_basic_auth_scripts_also_open_the_vault(secured):
    c, cfg, svc = secured
    token = base64.b64encode(b"jerome:correct horse battery").decode()
    assert not svc.vault.is_unlocked("jerome")
    assert c.get("/api/documents", headers={"Authorization": f"Basic {token}"}).status_code == 200
    assert svc.vault.is_unlocked("jerome")


def test_filter_lists_only_offer_what_exists_given_the_other_filters(client, services, tmp_path, no_model):
    def doc(name, text, folder, day):
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        upload(client, f, name=f"{folder}/{name}")
        return client.get("/api/documents", params={"limit": 50}).json()["items"]

    doc("paie1.txt", "BULLETIN DE SALAIRE Période du 01/03/2019 au 31/03/2019\nSalaire brut 4 000\nCotisations 880\nNet à payer 3 000\nSalaire de base", "Travail", 0)
    doc("paie2.txt", "BULLETIN DE SALAIRE Période du 01/03/2021 au 31/03/2021\nSalaire brut 4 100\nCotisations 890\nNet à payer 3 100\nSalaire de base", "Travail", 0)
    doc("facture.txt", "FACTURE n° 7 Date de facture : 05/11/2024\nMontant HT 100 TVA 20 Total TTC 120", "Maison", 0)
    facets = lambda **kw: client.get("/api/facets", params=kw).json()
    everything = facets()
    assert [y["year"] for y in everything["years"]] == ["2024", "2021", "2019"]
    assert {t["name"] for t in everything["types"]} == {"Fiches de paie", "Factures"}
    # a type narrows the years, a year narrows the types, a folder narrows both
    assert [y["year"] for y in facets(type="Fiches de paie")["years"]] == ["2021", "2019"]
    assert [t["name"] for t in facets(year="2024")["types"]] == ["Factures"]
    assert [y["year"] for y in facets(folder="Maison")["years"]] == ["2024"]
    # a filter never narrows its own list: with the type chosen, the other types stay available for that year
    assert {t["name"] for t in facets(type="Fiches de paie", year="2021")["types"]} == {"Fiches de paie"}
    assert {t["name"] for t in facets(type="Factures")["types"]} == {"Fiches de paie", "Factures"}
    assert [y["year"] for y in facets(type="Fiches de paie", year="2021")["years"]] == ["2021", "2019"]
    assert all(t["count"] >= 1 for t in everything["tags"])

    # the same in the search results
    r = client.get("/api/search", params={"q": "salaire"}).json()
    assert [y["year"] for y in r["facets"]["years"]] == ["2021", "2019"] and [t["name"] for t in r["facets"]["types"]] == ["Fiches de paie"]
    r = client.get("/api/search", params={"q": "salaire", "date_from": "2021-01-01", "date_to": "2021-12-31"}).json()
    assert [d["original_name"] for d in r["items"]] == ["paie2.txt"] and r["total"] == 1
    assert [y["year"] for y in r["facets"]["years"]] == ["2021", "2019"]          # the year list keeps its other years
    r = client.get("/api/search", params={"q": "facture salaire", "type": "Factures"}).json()
    assert r["items"] == []                                                       # every word is required
    r = client.get("/api/search", params={"q": "total", "type": "Factures"}).json()
    assert [d["original_name"] for d in r["items"]] == ["facture.txt"]
