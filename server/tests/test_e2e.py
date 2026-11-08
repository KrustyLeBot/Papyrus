"""End to end: the group tree built from the generated documents matches the examples of the specification."""
import pytest

from . import docgen
from .conftest import MODEL_DIR, drain, requires_tesseract, upload


def tree(client):
    items = client.get("/api/groups").json()["items"]
    by_id = {g["id"]: g for g in items}

    def path(g):
        return (path(by_id[g["parent_id"]]) + " / " if g["parent_id"] else "") + g["name"]

    return {path(g): g for g in items}


def members(client, group_id):
    return sorted(d["title"] for d in client.get("/api/documents", params={"group": group_id, "children": "0"}).json()["items"])


def build_corpus(client, services, tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    for m in (7, 8, 9):
        upload(client, docgen.payslip_fr(d / f"morel-{m}.pdf", "Atelier Morel SAS", "73282932000074", m, 2026))
    for m in (7, 8):
        upload(client, docgen.payslip_fr(d / f"ravel-{m}.pdf", "Cabinet Ravel SARL", "35600000000048", m, 2026))
    for m in (7, 8, 9):
        upload(client, docgen.payslip_ch(d / f"helvetia-{m}.pdf", "Helvetia Logistique SA", "CHE-116.281.710", m, 2026))
    upload(client, docgen.immo(d / "acte-appart.pdf", "acte", "12 rue des Tanneurs", "69001 Lyon", "AB 45", "09/03/2022"))
    upload(client, docgen.immo(d / "compromis-appart.pdf", "compromis", "12 rue des Tanneurs", "69001 Lyon", "AB 45", "10/01/2022"))
    upload(client, docgen.immo(d / "acte-maison.pdf", "acte", "4 chemin du Crêt", "74000 Annecy", "CD 123", "14/10/2026"))
    upload(client, docgen.immo(d / "pret-maison.pdf", "pret", "4 chemin du Crêt", "74000 Annecy", "CD 123", "02/09/2026"))
    upload(client, docgen.invoice(d / "facture-1.pdf", "Plomberie Dupont SARL", "35600000000048", 101, "03/11/2026"))
    upload(client, docgen.invoice(d / "edf.pdf", "Électricité de Lyon SA", "73282932000074", 7, "05/11/2026", energy=True))
    upload(client, docgen.docx(d / "contrat.docx", ["Contrat de travail", "Période d'essai de trois mois", "Fait le 02/01/2026"]))
    drain(services)


def check_tree(client):
    t = tree(client)
    assert {"Fiches de paie", "Immobilier"} <= set(t)
    pay = {k for k in t if k.startswith("Fiches de paie / ")}
    assert pay == {"Fiches de paie / Atelier Morel SAS", "Fiches de paie / Cabinet Ravel SARL", "Fiches de paie / Helvetia Logistique SA"}
    assert len(members(client, t["Fiches de paie / Atelier Morel SAS"]["id"])) == 3
    assert len(members(client, t["Fiches de paie / Cabinet Ravel SARL"]["id"])) == 2
    assert len(members(client, t["Fiches de paie / Helvetia Logistique SA"]["id"])) == 3
    homes = {k for k in t if k.startswith("Immobilier / ")}
    assert homes == {"Immobilier / 12 rue des Tanneurs, 69001 Lyon", "Immobilier / 4 chemin du Crêt, 74000 Annecy"}
    assert len(members(client, t["Immobilier / 12 rue des Tanneurs, 69001 Lyon"]["id"])) == 2
    assert len(members(client, t["Immobilier / 4 chemin du Crêt, 74000 Annecy"]["id"])) == 2
    # the notary's address is shared by both purchases: it never becomes a group
    assert not any("Mairie" in k for k in t)
    assert "Factures" in t and "Énergie" in t
    assert "Emploi" in t


def test_group_tree_without_model(client, services, tmp_path, no_model):
    build_corpus(client, services, tmp_path)
    check_tree(client)
    docs = client.get("/api/documents", params={"limit": 100}).json()["items"]
    assert all(d["status"] == "done" for d in docs) and len(docs) == 15
    assert all(d["groups"] for d in docs)  # always in at least one group
    assert any(t["name"] == "2026" for d in docs for t in d["tags"])
    # a document of the immobilier type that is also a tax paper may belong to several groups
    payslip = next(d for d in docs if "morel-8" in d["original_name"] or d["title"].lower().startswith("morel 8"))
    assert payslip["date"] == "2026-08-31" and payslip["date_source"] == "detected"


@pytest.mark.skipif(not MODEL_DIR, reason="set PAPYRUS_TEST_MODEL to a folder holding the embeddings model")
def test_group_tree_with_model(client, services, tmp_path):
    assert services.embedder.available
    build_corpus(client, services, tmp_path)
    check_tree(client)
    docs = client.get("/api/documents", params={"limit": 100}).json()["items"]
    first = docs[0]["id"]
    sim = client.get(f"/api/documents/{first}/similar").json()["items"]
    assert sim and sim[0]["score"] > 0.8
    hits = client.get("/api/search", params={"q": "bulletin de paie"}).json()
    assert hits["semantic"] and hits["items"]
    # only real matches: an invoice or a property paper is not an answer to "bulletin de paie"
    groups = {g["path"].split(" / ")[0] for d in hits["items"] for g in d["groups"]}
    assert groups <= {"Fiches de paie", "Emploi"}, groups


def test_manual_assignment_survives_reclassification_and_teaches(client, services, tmp_path, no_model):
    build_corpus(client, services, tmp_path)
    t = tree(client)
    ravel = t["Fiches de paie / Cabinet Ravel SARL"]["id"]
    docs = client.get("/api/documents", params={"limit": 100}).json()["items"]
    morel_doc = next(d for d in docs if d["groups"][0]["path"] == "Fiches de paie / Atelier Morel SAS")
    r = client.post(f"/api/groups/{ravel}/documents", json={"doc_ids": [morel_doc["id"]], "from_group": morel_doc["groups"][0]["id"]})
    assert r.status_code == 200
    client.post("/api/groups/reclassify", json={"group_id": None})
    after = client.get(f"/api/documents/{morel_doc['id']}").json()
    assert [g["path"] for g in after["groups"] if g.get("source") == "manual"] == ["Fiches de paie / Cabinet Ravel SARL"]


def test_locked_document_is_not_reclassified(client, services, tmp_path, no_model):
    build_corpus(client, services, tmp_path)
    docs = client.get("/api/documents", params={"limit": 100}).json()["items"]
    target = next(d for d in docs if d["groups"][0]["path"] == "Fiches de paie / Helvetia Logistique SA")
    client.patch(f"/api/documents/{target['id']}", json={"locked": True})
    helv = target["groups"][0]["id"]
    client.post(f"/api/groups/{helv}/documents", json={"doc_ids": [target["id"]]})
    before = [g["path"] for g in client.get(f"/api/documents/{target['id']}").json()["groups"]]
    client.post("/api/groups/reclassify", json={"group_id": None})
    after = [g["path"] for g in client.get(f"/api/documents/{target['id']}").json()["groups"]]
    assert before == after


def test_group_operations(client, services, tmp_path, no_model):
    build_corpus(client, services, tmp_path)
    t = tree(client)
    a, b = t["Immobilier / 12 rue des Tanneurs, 69001 Lyon"]["id"], t["Immobilier / 4 chemin du Crêt, 74000 Annecy"]["id"]
    # merge
    assert client.post(f"/api/groups/{a}/merge", json={"into": b}).status_code == 200
    assert len(members(client, b)) == 4
    assert client.get(f"/api/groups/{a}").status_code == 404
    # rename, create, move, delete (documents go up to the parent)
    client.patch(f"/api/groups/{b}", json={"name": "Biens"})
    sub = client.post("/api/groups", json={"name": "Sous", "parent_id": b}).json()["id"]
    assert client.patch(f"/api/groups/{b}", json={"move": True, "parent_id": sub}).status_code == 400  # into itself
    client.delete(f"/api/groups/{b}")
    t = tree(client)
    assert "Immobilier / Sous" in t and not any("Biens" in k for k in t)
    imm = t["Immobilier"]
    assert len(members(client, imm["id"])) == 4
    assert client.delete(f"/api/groups/{next(g['id'] for g in client.get('/api/groups').json()['items'] if g['inbox'])}").status_code == 400


@requires_tesseract
def test_noisy_scan_is_ocrised_and_classified(client, services, tmp_path, no_model):
    scan = docgen.noisy_scan(tmp_path / "scan.png", ["FACTURE n° 4521", "Date de facture : 05/11/2026", "Montant HT 100,00", "TVA 20,00", "Total TTC 120,00"])
    r = upload(client, scan)
    drain(services)
    doc = client.get(f"/api/documents/{r['id']}").json()
    assert doc["status"] == "done" and doc["ocr"]
    assert "FACTURE" in doc["text"].upper()
    assert any(g["path"].startswith("Factures") for g in doc["groups"])


@requires_tesseract
@pytest.mark.parametrize("angle", [90, 180, 270])
def test_sideways_scan_is_read_upright(client, services, tmp_path, no_model, angle):
    from PIL import Image

    lines = ["DIPLOME DU BACCALAUREAT GENERAL", "Academie de Grenoble", "Fait a Grenoble, le 22 Septembre 2015",
             "Mention bien", "Ministere de l'education nationale", "Signature du titulaire"]
    upright = docgen.noisy_scan(tmp_path / "up.png", lines)
    turned = tmp_path / "turned.png"
    Image.open(upright).rotate(angle, expand=True).save(turned)
    r = upload(client, turned)
    drain(services)
    doc = client.get(f"/api/documents/{r['id']}").json()
    assert "BACCALAUREAT" in doc["text"].upper()
    assert doc["date"] == "2015-09-22" and doc["date_source"] == "detected"
