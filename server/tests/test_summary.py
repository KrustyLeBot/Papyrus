from collections import Counter

from papyrus import summary as S

from . import docgen
from .conftest import drain, upload


def test_junk_names_versus_real_names():
    for junk in ("scan0012", "IMG_20240315_101500", "Document (3)", "123456", "a3f9c2d1-77b0-4c11-9a0e-5d2c1b7e8f90", "Scan", "DSC01234"):
        assert S.is_junk_name(junk), junk
    for real in ("Diplome BAC", "contrat-travail-2017", "Taxe fonciere 2026", "releve octobre"):
        assert not S.is_junk_name(real), real


def test_title_from_the_heading_not_the_letterhead():
    text = ("RÉPUBLIQUE FRANÇAISE\nMINISTÈRE DE L'ÉDUCATION NATIONALE\nACADÉMIE DE GRENOBLE\n"
            "DIPLÔME DU BACCALAURÉAT GÉNÉRAL\nVu le procès-verbal de l'examen du jury.")
    assert S.derive_title(text) == "Diplôme du baccalauréat général"
    assert S.derive_title("12 34 56\n@@ ##\n") is None
    assert S.derive_title("ATELIER MOREL SAS\nBULLETIN DE SALAIRE\nPériode du 01/09/2026") == "Bulletin de salaire"


def test_keywords_are_the_words_rare_in_the_archive():
    text = "Le baccalauréat est délivré par l'académie. Le baccalauréat atteste le niveau. Mention bien, académie de Grenoble."
    counts, forms = S.term_stats(text)
    assert "pour" not in counts and "avec" not in counts
    scores = S.term_scores(counts, {"academie": 9, "grenoble": 1}, 10)  # "académie" is in 9 of 10 documents
    kws = S.keywords(text, "Diplome", counts, forms, scores)
    assert kws[0] == "baccalauréat" and "académie" not in kws


def test_extractive_summary_quotes_representative_sentences():
    text = ("Le présent contrat de travail à durée déterminée est conclu entre l'association et le salarié pour une saison.\n\n"
            "Salaire brut 2 000,00 euros par mois, 35 heures.\n\n"
            "Le salarié exercera les fonctions d'entretien de la base nautique et de gardiennage du matériel.\n\n"
            "Cordialement, merci de retourner un exemplaire signé.")
    counts, _ = S.term_stats(text)
    scores = S.term_scores(counts, {}, 5)
    digest = S.extractive_summary(text, scores)
    assert "contrat de travail" in digest and "2 000,00" not in digest  # figures lines are not prose
    assert len(digest) <= 421


def test_pipeline_gives_title_summary_and_keyword_tags(client, services, tmp_path, no_model):
    f = tmp_path / "scan0012.pdf"
    docgen.pdf(f, ["REPUBLIQUE FRANCAISE", "ATTESTATION DE STAGE", "Nous attestons que Monsieur Jean Exemple a effectue un stage",
                   "de six semaines dans notre laboratoire de recherche en hydrologie.", "Le stagiaire a etudie la fonte des glaciers.",
                   "Hydrologie glaciers laboratoire."])
    doc_id = upload(client, f)["id"]
    drain(services)
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["title"] == "Attestation de stage"
    assert doc["summary"] and "stage" in doc["summary"].lower()
    names = {t["name"] for t in doc["tags"]}
    assert "attestation" in names and ({"hydrologie", "glaciers"} & names)
    # a title typed by hand is never replaced
    client.patch(f"/api/documents/{doc_id}", json={"title": "Mon stage"})
    client.post(f"/api/documents/{doc_id}/reprocess")
    drain(services)
    assert client.get(f"/api/documents/{doc_id}").json()["title"] == "Mon stage"
    assert client.get("/api/documents").json()["items"][0]["summary"]
