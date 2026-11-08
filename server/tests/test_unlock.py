import pikepdf

from . import docgen
from .conftest import drain, place, upload


def test_owner_password_only_is_removed_silently(client, services, cfg, tmp_path, no_model):
    src = docgen.pdf(tmp_path / "owner.pdf", ["BULLETIN DE SALAIRE", "Net à payer 3 000,00"], owner_only=True)
    with pikepdf.open(src) as pdf:
        assert pdf.is_encrypted  # restricted, but opens without any password
    original = src.read_bytes()
    result = upload(client, src)
    assert result["status"] == "queued"
    doc = client.get(f"/api/documents/{result['id']}").json()
    assert doc["status"] == "done" and "Net à payer" in doc["text"]
    # the file in the source folder is not touched; the restriction-free copy lives under /data
    assert (cfg.source_dir / "owner.pdf").read_bytes() == original
    copy = next(cfg.unlocked_dir.glob("*.pdf"))
    with pikepdf.open(copy) as pdf:
        assert not pdf.is_encrypted
    # the same file somewhere else is the same document
    assert upload(client, src, name="archive/owner copie.pdf")["status"] == "duplicate"
    assert len(client.get(f"/api/documents/{result['id']}").json()["locations"]) == 2


def test_user_password_flow(client, services, cfg, tmp_path, no_model, capfd):
    secret = "tr0ub4dor-secret-pw"
    src = docgen.pdf(tmp_path / "locked.pdf", ["BULLETIN DE SALAIRE", "Salaire brut 4 000,00", "Net à payer 3 120,00", "cotisations"], user_password=secret)
    original = src.read_bytes()
    result = upload(client, src)
    assert result["status"] == "password_required"
    doc_id = result["id"]
    assert not list(cfg.unlocked_dir.iterdir())  # nothing decrypted, nothing read, nothing indexed
    assert client.get("/api/locked").json()["items"][0]["id"] == doc_id
    assert client.get("/api/queue").json()["locked"] == 1
    assert client.get("/api/search", params={"q": "salaire"}).json()["items"] == []
    assert client.get("/api/documents").json()["items"] == []
    assert client.get(f"/api/documents/{doc_id}/file").status_code == 409

    bad = client.post(f"/api/documents/{doc_id}/unlock", json={"password": "wrong"})
    assert bad.status_code == 400 and "incorrect" in bad.json()["detail"]
    assert client.get("/api/queue").json()["locked"] == 1  # still waiting, retry without limit
    assert client.post(f"/api/documents/{doc_id}/unlock", json={"password": "wrong again"}).status_code == 400

    ok = client.post(f"/api/documents/{doc_id}/unlock", json={"password": secret})
    assert ok.status_code == 200 and "password" not in ok.text
    drain(services)
    assert (cfg.source_dir / "locked.pdf").read_bytes() == original  # the original stays encrypted where it is
    copies = list(cfg.unlocked_dir.glob("*.pdf"))
    assert len(copies) == 1
    with pikepdf.open(copies[0]) as pdf:  # opens with no password
        assert not pdf.is_encrypted
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["status"] == "done" and doc["unlocked_at"] and "Net à payer" in doc["text"]
    assert client.get(f"/api/documents/{doc_id}/file").status_code == 200  # the preview is the unlocked copy
    assert upload(client, src, name="ailleurs/locked.pdf")["status"] == "duplicate"

    # the password is kept (by the document's hash) but only encrypted: never in clear in the database, the files, the
    # logs, the export or any API answer
    assert client.get("/api/admin/status").json()["passwords"] == 1
    for path in cfg.data_dir.rglob("*"):
        if path.is_file() and path.name != "secret.key":
            assert secret.encode() not in path.read_bytes(), path
    assert (cfg.data_dir / "secret.key").stat().st_mode & 0o077 == 0  # the key file is private
    out, err = capfd.readouterr()
    assert secret not in out + err
    assert secret not in str(client.get("/api/documents").json())


def test_unlock_tries_the_same_password_on_other_waiting_pdfs(client, services, cfg, tmp_path, no_model):
    shared = "shared-pw-2026"
    ids = []
    for month in (1, 2, 3):
        p = docgen.payslip_fr(tmp_path / f"s{month}.pdf", "Atelier Morel SAS", "73282932000074", month, 2026, password=shared)
        ids.append(upload(client, p)["id"])
    other = docgen.pdf(tmp_path / "other.pdf", ["Autre document protégé"], user_password="different")
    other_id = upload(client, other)["id"]
    r = client.post(f"/api/documents/{ids[0]}/unlock", json={"password": shared, "try_others": True}).json()
    assert {o["id"] for o in r["others"]} == set(ids[1:])
    assert r["remaining"] == 1
    assert client.get("/api/locked").json()["items"][0]["id"] == other_id
    drain(services)
    assert client.get("/api/documents").json()["total"] == 3


def test_saved_passwords_open_later_protected_pdfs_by_themselves(client, services, cfg, tmp_path, no_model):
    first = upload(client, docgen.payslip_fr(tmp_path / "s1.pdf", "Atelier Morel SAS", "73282932000074", 1, 2026, password="mon-mot-de-passe"))
    assert first["status"] == "password_required"
    assert client.post(f"/api/documents/{first['id']}/unlock", json={"password": "mon-mot-de-passe"}).status_code == 200
    drain(services)
    # a new protected file with the same password turns up in a folder: it opens without asking
    later = upload(client, docgen.payslip_fr(tmp_path / "s2.pdf", "Atelier Morel SAS", "73282932000074", 2, 2026, password="mon-mot-de-passe"),
                   name="Paie/fevrier.pdf")
    assert later["status"] == "queued"
    assert client.get("/api/documents/%d" % later["id"]).json()["status"] == "done"
    assert client.get("/api/locked").json()["items"] == []
    # another password is still asked
    other = upload(client, docgen.pdf(tmp_path / "x.pdf", ["autre"], user_password="un-autre"))
    assert other["status"] == "password_required"
    # the decrypted copy is lost (a restored data folder): the analysis decrypts again with the saved password
    for copy in cfg.unlocked_dir.glob("*.pdf"):
        copy.unlink()
    client.post("/api/admin/reprocess-all")
    drain(services)
    assert client.get(f"/api/documents/{first['id']}").json()["status"] == "done" and any(cfg.unlocked_dir.glob("*.pdf"))
    # forgetting the saved passwords: the next protected PDF waits again
    assert client.post("/api/admin/forget-passwords").json()["passwords"] >= 1
    third = upload(client, docgen.payslip_fr(tmp_path / "s3.pdf", "Atelier Morel SAS", "73282932000074", 3, 2026, password="mon-mot-de-passe"))
    assert third["status"] == "password_required"


def test_password_not_kept_when_the_user_says_so(client, services, tmp_path, no_model):
    a = upload(client, docgen.pdf(tmp_path / "a.pdf", ["a"], user_password="pw-secret-1"))["id"]
    assert client.post(f"/api/documents/{a}/unlock", json={"password": "pw-secret-1", "remember": False}).status_code == 200
    assert client.get("/api/admin/status").json()["passwords"] == 0
    assert upload(client, docgen.pdf(tmp_path / "b.pdf", ["b"], user_password="pw-secret-1"))["status"] == "password_required"


def test_removing_a_document_forgets_its_password(client, services, tmp_path, no_model):
    a = upload(client, docgen.pdf(tmp_path / "a.pdf", ["a"], user_password="pw-secret-2"))["id"]
    client.post(f"/api/documents/{a}/unlock", json={"password": "pw-secret-2"})
    assert client.get("/api/admin/status").json()["passwords"] == 1
    client.delete(f"/api/documents/{a}")
    assert client.get("/api/admin/status").json()["passwords"] == 0


def test_try_others_off(client, services, tmp_path, no_model):
    a = upload(client, docgen.pdf(tmp_path / "a.pdf", ["a"], user_password="pw"))["id"]
    upload(client, docgen.pdf(tmp_path / "b.pdf", ["b"], user_password="pw"))
    r = client.post(f"/api/documents/{a}/unlock", json={"password": "pw", "try_others": False}).json()
    assert r["others"] == [] and r["remaining"] == 1
