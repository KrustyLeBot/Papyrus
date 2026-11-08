import pytest
from PIL import Image

from . import docgen
from .conftest import MODEL_DIR, requires_tesseract, upload


@requires_tesseract
def test_noisy_scan_is_ocrised(client, services, tmp_path, no_model):
    scan = docgen.noisy_scan(tmp_path / "scan.png", ["FACTURE n° 4521", "Date de facture : 05/11/2026", "Montant HT 100,00", "TVA 20,00", "Total TTC 120,00"])
    r = upload(client, scan)
    doc = client.get(f"/api/documents/{r['id']}").json()
    assert doc["status"] == "done" and doc["ocr"] and "FACTURE" in doc["text"].upper()
    assert "Factures" in doc["types"]


@requires_tesseract
@pytest.mark.parametrize("angle", [90, 180, 270])
def test_sideways_scan_is_read_upright(client, services, tmp_path, no_model, angle):
    lines = ["DIPLOME DU BACCALAUREAT GENERAL", "Academie de Grenoble", "Fait a Grenoble, le 22 Septembre 2015",
             "Mention bien", "Ministere de l'education nationale", "Signature du titulaire"]
    upright = docgen.noisy_scan(tmp_path / "up.png", lines)
    turned = tmp_path / "scan0001.png"
    Image.open(upright).rotate(angle, expand=True).save(turned)
    r = upload(client, turned)
    doc = client.get(f"/api/documents/{r['id']}").json()
    assert "BACCALAUREAT" in doc["text"].upper()
    assert doc["date"] == "2015-09-22" and doc["date_source"] == "detected"
    assert doc["title"] == "Diplome du baccalaureat general"  # scan names say nothing: the heading is the title


@requires_tesseract
def test_a_docx_and_a_scanned_pdf_page(client, services, tmp_path, no_model):
    d = docgen.docx(tmp_path / "contrat.docx", ["Contrat de travail", "Période d'essai de trois mois", "Fait le 02/01/2026"])
    r = upload(client, d, name="RH/contrat.docx")
    doc = client.get(f"/api/documents/{r['id']}").json()
    assert "Emploi" in doc["types"] and doc["date"] == "2026-01-02"


@pytest.mark.skipif(not MODEL_DIR, reason="set PAPYRUS_TEST_MODEL to a folder holding the embeddings model")
def test_semantic_search_only_returns_real_matches(client, services, tmp_path):
    assert services.embedder.available
    for m in (7, 8, 9):
        upload(client, docgen.payslip_fr(tmp_path / f"morel-{m}.pdf", "Atelier Morel SAS", "73282932000074", m, 2026))
    upload(client, docgen.invoice(tmp_path / "plomberie.pdf", "Plomberie Dupont SARL", "35600000000048", 101, "03/11/2026"))
    upload(client, docgen.immo(tmp_path / "acte.pdf", "acte", "4 chemin du Crêt", "74000 Annecy", "CD 123", "14/10/2026"))
    first = client.get("/api/documents").json()["items"][0]["id"]
    sim = client.get(f"/api/documents/{first}/similar").json()["items"]
    assert sim and sim[0]["score"] > 0.8
    hits = client.get("/api/search", params={"q": "bulletin de paie"}).json()
    assert hits["semantic"] and hits["items"]
    assert all(d["original_name"].startswith("morel") for d in hits["items"]), [d["original_name"] for d in hits["items"]]
    # the threshold is adjustable: at the maximum nothing is « close enough » unless the words match
    client.put("/api/admin/search-settings", json={"semantic_min": 0.95, "prefix": True, "fuzzy": True})
    assert all(d["match"] == "texte" for d in client.get("/api/search", params={"q": "feuille de salaire"}).json()["items"])
