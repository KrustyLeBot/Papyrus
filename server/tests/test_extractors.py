import datetime as dt

from papyrus import dates, identifiers as ident

TODAY = dt.date(2026, 11, 8)


def test_dates_french_formats():
    assert dates.detect("Fait le 12/03/2026", TODAY) == dt.date(2026, 3, 12)
    assert dates.detect("Émis le 12.03.2026", TODAY) == dt.date(2026, 3, 12)
    assert dates.detect("Paris, le 12 mars 2026", TODAY) == dt.date(2026, 3, 12)
    assert dates.detect("Facture du 1er février 2025", TODAY) == dt.date(2025, 2, 1)
    assert dates.detect("Relevé de mars 2026", TODAY) == dt.date(2026, 3, 1)
    assert dates.detect("Date : 2026-03-12", TODAY) == dt.date(2026, 3, 12)


def test_dates_keywords_win_and_absurd_dates_dropped():
    text = "Né le 04/05/1980\nPériode du 01/09/2026 au 30/09/2026\nDate de paiement : 30/09/2026"
    assert dates.detect(text, TODAY) == dt.date(2026, 9, 30)
    assert dates.detect("Valable jusqu'au 01/01/2090", TODAY) is None
    assert dates.detect("Document de 12/03/1920", TODAY) is None
    assert dates.detect("aucune date ici", TODAY) is None


def test_siret_luhn():
    assert ident.luhn_ok("73282932000074")
    assert not ident.luhn_ok("73282932000075")
    keys = ident.extract("ATELIER MOREL SAS\nSIRET : 732 829 320 00074")
    siret = [k for k in keys if k.key == "siret"]
    assert siret and siret[0].value == "73282932000074" and siret[0].label == "Atelier Morel SAS"
    assert not [k for k in ident.extract("SIRET : 732 829 320 00075") if k.key == "siret"]


def test_swiss_uid():
    assert ident.che_ok("116281710")
    assert not ident.che_ok("116281711")
    keys = ident.extract("HELVETIA LOGISTIQUE SA\nIDE : CHE-116.281.710")
    uid = [k for k in keys if k.key == "che_uid"]
    assert uid and uid[0].value == "116281710" and uid[0].label == "Helvetia Logistique SA"


def test_address_and_cadastre():
    text = "DÉSIGNATION DU BIEN : maison sise 4 chemin du Crêt, 74000 Annecy\nCadastrée section AB n° 123"
    keys = {k.key: k for k in ident.extract(text)}
    assert keys["property_address"].value == "4 chemin du cret 74000 annecy"
    assert keys["property_address"].label == "4 chemin du Crêt, 74000 Annecy"
    assert keys["cadastre_ref"].value == "AB-123"


def test_property_address_preferred_over_notary_address():
    text = ("Maître Claire Durand, notaire, 3 place de la Mairie, 74000 Annecy\n"
            "DÉSIGNATION DU BIEN : maison sise 12 rue des Tanneurs, 69001 Lyon")
    addresses = [k for k in ident.extract(text) if k.key == "property_address"]
    assert addresses[0].value.startswith("12 rue des tanneurs")
    assert len(addresses) == 1


def test_contract_number():
    keys = ident.extract("Contrat n° AH-2024-5567 Assurance habitation")
    assert [k.value for k in keys if k.key == "contract_no"] == ["AH-2024-5567"]


def test_masking_iban_and_nir():
    iban = "FR76 3000 6000 0112 3456 7890 189"
    assert ident.iban_ok(iban)
    nir = "1 85 05 78 006 084 91"
    assert ident.nir_ok(nir)
    text, found = ident.mask_sensitive(f"IBAN {iban}\nN° SS {nir}\nRéférence 1234")
    assert "3000 6000" not in text and "006 084" not in text
    assert "FR76" in text and text.rstrip().endswith("Référence 1234")
    assert {f["kind"] for f in found} == {"iban", "nir"}


def test_masking_leaves_invalid_numbers_alone():
    text, found = ident.mask_sensitive("Réf FR76 0000 0000 0000 0000 0000 001 et 1 85 05 78 006 084 92")
    assert not found and "0000 0000" in text


def test_swiss_avs_masked():
    text, found = ident.mask_sensitive("N° AVS 756.1234.5678.97")
    assert found and "1234.5678" not in text
