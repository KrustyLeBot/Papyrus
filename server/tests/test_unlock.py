import sqlite3

import pikepdf

from . import docgen
from .conftest import drain, upload


def read_all(path):
    return path.read_bytes()


def test_owner_password_only_is_removed_silently(client, services, cfg, tmp_path, no_model):
    src = docgen.pdf(tmp_path / "owner.pdf", ["BULLETIN DE SALAIRE", "Net à payer 3 000,00"], owner_only=True)
    with pikepdf.open(src) as pdf:
        assert pdf.is_encrypted  # restricted, but opens without any password
    result = upload(client, src)
    assert result["status"] == "queued"
    drain(services)
    doc = client.get(f"/api/documents/{result['id']}").json()
    assert doc["status"] == "done" and "Net à payer" in doc["text"]
    stored = next((cfg.files_dir).rglob("*.pdf"))
    with pikepdf.open(stored) as pdf:
        assert not pdf.is_encrypted
    # the encrypted original's hash is remembered: re-importing it is a duplicate
    assert upload(client, src)["status"] == "duplicate"


def test_user_password_flow(client, services, cfg, tmp_path, no_model, capfd):
    secret = "tr0ub4dor-secret-pw"
    src = docgen.pdf(tmp_path / "locked.pdf", ["BULLETIN DE SALAIRE", "Salaire brut 4 000,00", "Net à payer 3 120,00", "cotisations"], user_password=secret)
    result = upload(client, src)
    assert result["status"] == "password_required"
    doc_id = result["id"]
    assert (cfg.locked_dir / next(cfg.locked_dir.iterdir()).name).exists()
    drain(services)  # nothing to process: the file is neither OCRed nor indexed
    assert client.get("/api/locked").json()["items"][0]["id"] == doc_id
    assert client.get("/api/queue").json()["locked"] == 1
    assert client.get("/api/search", params={"q": "salaire"}).json()["items"] == []
    assert client.get(f"/api/documents/{doc_id}/file").status_code == 409

    bad = client.post(f"/api/documents/{doc_id}/unlock", json={"password": "wrong"})
    assert bad.status_code == 400 and "incorrect" in bad.json()["detail"]
    assert client.get("/api/queue").json()["locked"] == 1  # still waiting, retry without limit
    assert client.post(f"/api/documents/{doc_id}/unlock", json={"password": "wrong again"}).status_code == 400

    ok = client.post(f"/api/documents/{doc_id}/unlock", json={"password": secret})
    assert ok.status_code == 200 and "password" not in ok.text
    drain(services)
    assert not any(cfg.locked_dir.iterdir())  # the encrypted copy is gone
    stored = [p for p in cfg.files_dir.rglob("*.pdf")]
    assert len(stored) == 1
    with pikepdf.open(stored[0]) as pdf:  # opens with no password
        assert not pdf.is_encrypted
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["status"] == "done" and doc["unlocked_at"]
    assert upload(client, src)["status"] == "duplicate"  # same protected file again

    # the password is nowhere: not in the database, the files, the logs or any API answer
    for path in (cfg.data_dir).rglob("*"):
        if path.is_file() and path.suffix in (".db", ".db-wal", ".db-shm"):
            assert secret.encode() not in path.read_bytes()
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
    assert client.get("/api/groups").json()["items"]  # the three payslips were grouped


def test_try_others_off(client, services, tmp_path, no_model):
    a = upload(client, docgen.pdf(tmp_path / "a.pdf", ["a"], user_password="pw"))["id"]
    b = upload(client, docgen.pdf(tmp_path / "b.pdf", ["b"], user_password="pw"))["id"]
    r = client.post(f"/api/documents/{a}/unlock", json={"password": "pw", "try_others": False}).json()
    assert r["others"] == [] and r["remaining"] == 1
