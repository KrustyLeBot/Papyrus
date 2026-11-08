"""The scanner keeps the database in line with the folders the user keeps: moves, renames, copies, edits, deletions."""
import hashlib
import os
import time

import pytest

from . import docgen
from .conftest import drain, place


def tree_hash(root):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out


def docs(client, **kw):
    return client.get("/api/documents", params={"limit": 200, **kw}).json()["items"]


@pytest.fixture
def pdfs(tmp_path):
    d = tmp_path / "make"
    d.mkdir()
    return {
        "paie": docgen.payslip_fr(d / "paie.pdf", "Atelier Morel SAS", "73282932000074", 9, 2026),
        "facture": docgen.invoice(d / "facture.pdf", "Plomberie Dupont SARL", "35600000000048", 101, "03/11/2026"),
        "acte": docgen.immo(d / "acte.pdf", "acte", "4 chemin du Crêt", "74000 Annecy", "CD 123", "14/10/2026"),
    }


def scan(services):
    result = services.scanner.scan()
    drain(services)
    return result


def test_first_scan_finds_documents_in_any_folder_and_never_writes(client, services, cfg, pdfs, no_model):
    place(services, pdfs["paie"], "Travail/Paie 2026/septembre.pdf")
    place(services, pdfs["facture"], "Bordel/n'importe quoi/facture.pdf")
    (cfg.source_dir / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
    (cfg.source_dir / "notes.docx.tmp").write_bytes(b"x")
    before = tree_hash(cfg.source_dir)
    result = scan(services)
    assert result["new"] == 2 or result["new"] == 3  # the png is wanted by the test config, but unreadable as an image
    items = docs(client)
    assert {d["folder"] for d in items if d["status"] == "done"} == {"Travail/Paie 2026", "Bordel/n'importe quoi"}
    assert tree_hash(cfg.source_dir) == before  # the source folder was only read


def test_default_extensions_only_pdf_and_docx(tmp_path):
    from papyrus.config import Config

    assert Config.from_env().extensions == frozenset({"pdf", "docx"})


def test_excluded_names_are_skipped(client, services, cfg, pdfs, no_model):
    for rel in ("@eaDir/x/paie.pdf", "#recycle/paie2.pdf", ".hidden/paie3.pdf", "~$lock.pdf", "Dossier/Thumbs.db"):
        place(services, pdfs["paie"], rel)
    place(services, pdfs["facture"], "ok/facture.pdf")
    services.scanner.scan()
    assert [d["path"] for d in docs(client, status="all")] == ["ok/facture.pdf"]


def test_unchanged_files_are_not_read_again(client, services, cfg, pdfs, no_model, monkeypatch):
    place(services, pdfs["paie"], "a/paie.pdf")
    scan(services)
    from papyrus import scanner

    calls = []
    real = scanner.sha256_file
    monkeypatch.setattr(scanner, "sha256_file", lambda p: calls.append(p) or real(p))
    result = services.scanner.scan()
    assert calls == [] and result["unchanged"] == 1 and result["new"] == 0


def test_move_and_rename_keep_the_document_and_what_was_typed(client, services, cfg, pdfs, no_model):
    src = place(services, pdfs["paie"], "En vrac/paie.pdf")
    scan(services)
    doc = docs(client)[0]
    client.post(f"/api/documents/{doc['id']}/tags", json={"name": "à garder"})
    client.patch(f"/api/documents/{doc['id']}", json={"doc_date": "2020-05-06"})
    # the user moves and renames the file
    dst = cfg.source_dir / "Travail" / "Paie" / "Fiche de paie septembre.pdf"
    dst.parent.mkdir(parents=True)
    src.rename(dst)
    result = scan(services)
    assert result["relocated"] == 1 and result["removed"] == 1 and result["new"] == 0
    moved = docs(client)
    assert len(moved) == 1 and moved[0]["id"] == doc["id"]  # same document: same SHA-256
    assert moved[0]["path"] == "Travail/Paie/Fiche de paie septembre.pdf" and moved[0]["folder"] == "Travail/Paie"
    full = client.get(f"/api/documents/{doc['id']}").json()
    assert "à garder" in {t["name"] for t in full["tags"]} and full["date"] == "2020-05-06" and full["date_source"] == "manual"
    assert full["title"] == "Fiche de paie septembre"  # a meaningful name: the title follows it
    # the search finds it by its new folder
    assert [d["id"] for d in client.get("/api/search", params={"q": "Travail"}).json()["items"]] == [doc["id"]]
    assert client.get("/api/search", params={"q": "vrac"}).json()["items"] == []


def test_a_renamed_file_keeps_a_title_typed_by_hand(client, services, cfg, pdfs, no_model):
    src = place(services, pdfs["paie"], "paie.pdf")
    scan(services)
    doc = docs(client)[0]
    client.patch(f"/api/documents/{doc['id']}", json={"title": "Ma fiche de septembre"})
    src.rename(cfg.source_dir / "renommé.pdf")
    scan(services)
    assert client.get(f"/api/documents/{doc['id']}").json()["title"] == "Ma fiche de septembre"


def test_two_copies_are_one_document_with_two_locations(client, services, cfg, pdfs, no_model):
    place(services, pdfs["paie"], "A/paie.pdf")
    place(services, pdfs["paie"], "B/copie de paie.pdf")
    scan(services)
    items = docs(client)
    assert len(items) == 1 and items[0]["copies"] == 2
    folders = {f["path"]: f["count"] for f in client.get("/api/folders").json()["items"]}
    assert folders == {"A": 1, "B": 1}
    # deleting one copy leaves the document in place
    (cfg.source_dir / "A" / "paie.pdf").unlink()
    scan(services)
    again = docs(client)
    assert len(again) == 1 and again[0]["copies"] == 1 and again[0]["folder"] == "B"


def test_edit_in_place_keeps_the_document_and_analyses_the_new_content(client, services, cfg, pdfs, tmp_path, no_model):
    src = place(services, pdfs["paie"], "paie.pdf")
    scan(services)
    doc = docs(client)[0]
    client.post(f"/api/documents/{doc['id']}/tags", json={"name": "perso"})
    client.patch(f"/api/documents/{doc['id']}", json={"title": "Mon titre"})
    new = docgen.pdf(tmp_path / "v2.pdf", ["CONTRAT DE LOCATION", "Bail meublé entre le bailleur et le locataire", "Loyer 800 euros"])
    place(services, new, "paie.pdf")  # the same path, new content
    os.utime(src, (time.time() + 5, time.time() + 5))
    result = scan(services)
    assert result["updated"] == 1 and result["new"] == 0
    items = docs(client)
    assert len(items) == 1 and items[0]["id"] == doc["id"]
    full = client.get(f"/api/documents/{doc['id']}").json()
    assert "BAIL" in full["text"].upper() and full["title"] == "Mon titre"
    assert "perso" in {t["name"] for t in full["tags"]}


def test_a_vanished_file_is_hidden_not_deleted_and_comes_back(client, services, cfg, pdfs, no_model):
    src = place(services, pdfs["facture"], "Factures/plomberie.pdf")
    place(services, pdfs["paie"], "Paie/paie.pdf")  # other files stay: an empty source would look like an unmounted disk
    scan(services)
    doc = next(d for d in docs(client) if d["folder"] == "Factures")
    client.post(f"/api/documents/{doc['id']}/tags", json={"name": "important"})
    data = src.read_bytes()
    src.unlink()
    result = scan(services)
    assert result["missing"] == 1
    assert [d["folder"] for d in docs(client)] == ["Paie"]                          # hidden from the lists…
    assert client.get("/api/search", params={"q": "plomberie"}).json()["items"] == []  # …and from the search
    assert [f["path"] for f in client.get("/api/folders").json()["items"]] == ["Paie"]
    assert client.get("/api/admin/status").json()["by_status"] == {"done": 1, "missing": 1}  # …but still known
    src.write_bytes(data)                                                           # the file is back (any path)
    place(services, pdfs["facture"], "Autre dossier/retrouvée.pdf")
    src.unlink()
    result = scan(services)
    assert result["restored"] == 1
    back = {d["id"]: d for d in docs(client)}
    assert doc["id"] in back and back[doc["id"]]["folder"] == "Autre dossier"
    assert "important" in {t["name"] for t in client.get(f"/api/documents/{doc['id']}").json()["tags"]}
    assert [d["id"] for d in client.get("/api/search", params={"q": "plomberie"}).json()["items"]] == [doc["id"]]


def test_an_empty_or_missing_source_removes_nothing(client, services, cfg, pdfs, no_model):
    src = place(services, pdfs["paie"], "paie.pdf")
    scan(services)
    src.unlink()  # an empty source while documents are known: looks like an unmounted disk
    result = scan(services)
    assert result["removed"] == 0 and "Rien n'est retiré" in services.scanner.state["warning"]
    assert len(docs(client)) == 1
    import shutil
    shutil.rmtree(cfg.source_dir)
    scan(services)
    assert "introuvable" in services.scanner.state["warning"] and len(docs(client)) == 1


def test_files_still_being_written_wait_for_the_next_scan(client, services, cfg, pdfs, no_model):
    cfg.settle_seconds = 3600
    place(services, pdfs["paie"], "copie en cours.pdf", mtime=time.time())
    result = services.scanner.scan()
    assert result["waiting"] == 1 and docs(client) == []
    cfg.settle_seconds = 0
    assert scan(services)["new"] == 1


def test_large_files_and_symlinks_are_skipped(client, services, cfg, pdfs, no_model):
    cfg.max_file_mb = 1
    big = cfg.source_dir / "gros.pdf"
    big.write_bytes(b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10))
    place(services, pdfs["paie"], "ok.pdf")
    os.symlink(cfg.source_dir / "ok.pdf", cfg.source_dir / "lien.pdf")
    result = scan(services)
    assert result["skipped"] == 1
    assert [d["path"] for d in docs(client)] == ["ok.pdf"]


def test_a_document_removed_from_papyrus_stays_out_even_as_a_copy(client, services, cfg, pdfs, no_model):
    place(services, pdfs["paie"], "A/paie.pdf")
    scan(services)
    doc = docs(client)[0]
    client.delete(f"/api/documents/{doc['id']}")
    place(services, pdfs["paie"], "B/paie copie.pdf")
    result = scan(services)
    assert result["ignored"] >= 1 and docs(client) == []


def test_purge_missing_forgets_for_good(client, services, cfg, pdfs, no_model):
    src = place(services, pdfs["paie"], "paie.pdf")
    place(services, pdfs["facture"], "facture.pdf")
    scan(services)
    src.unlink()
    scan(services)
    assert client.post("/api/admin/purge-missing").json()["documents"] == 1
    assert client.get("/api/admin/status").json()["by_status"] == {"done": 1}
