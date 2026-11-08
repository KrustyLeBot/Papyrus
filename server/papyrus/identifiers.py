"""Identifiers found in a document's text: the keys that sort documents into sub-groups (§7.2), and the sensitive
numbers that must never be shown or indexed in full (IBAN, French NIR, Swiss AVS number).

Every extractor returns Key(key, value, label): `value` is normalised (what is compared), `label` is what a
sub-group created from it is called."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .text import fold

KEY_NAMES = ("siret", "siren", "che_uid", "tva", "cadastre_ref", "property_address", "contract_no",
             "employer_name", "company", "year")


@dataclass(frozen=True)
class Key:
    key: str
    value: str
    label: str
    position: int = 0


# ---------------------------------------------------------------- checksums

def luhn_ok(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def che_ok(digits: str) -> bool:
    """Swiss UID (IDE): 8 digits weighted 5,4,3,2,7,6,5,4, check digit = 11 - sum mod 11 (10 is never issued)."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], (5, 4, 3, 2, 7, 6, 5, 4)))
    check = 11 - total % 11
    if check == 11:
        check = 0
    return check != 10 and check == int(digits[8])


def tva_fr_ok(key: str, siren: str) -> bool:
    if not key.isdigit():
        return True  # the newer alphanumeric keys have no public checksum
    return int(key) == (12 + 3 * (int(siren) % 97)) % 97


def iban_ok(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    moved = iban[4:] + iban[:4]
    number = "".join(str(int(c, 36)) for c in moved)
    return int(number) % 97 == 1


def nir_ok(nir: str) -> bool:
    nir = nir.replace(" ", "").upper()
    if len(nir) != 15:
        return False
    body = nir[:13].replace("2A", "19").replace("2B", "18")
    if not body.isdigit() or not nir[13:].isdigit():
        return False
    return 97 - int(body) % 97 == int(nir[13:])


def ahv_ok(digits: str) -> bool:
    """Swiss AVS/AHV number (756.xxxx.xxxx.xx): EAN-13 check digit."""
    if len(digits) != 13 or not digits.startswith("756"):
        return False
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits[:12]))
    return (10 - total % 10) % 10 == int(digits[12])


# ---------------------------------------------------------------- sensitive numbers

_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?)\b")
_NIR = re.compile(r"(?<![\d])([12][ ]?\d{2}[ ]?(?:0[1-9]|1[0-2]|[2-9]\d)[ ]?(?:\d{2}|2[AB])[ ]?\d{3}[ ]?\d{3}[ ]?\d{2})(?![\d])")
_AHV = re.compile(r"(?<!\d)(756[. ]?\d{4}[. ]?\d{4}[. ]?\d{2})(?!\d)")


def mask_iban(iban: str) -> str:
    compact = iban.replace(" ", "")
    middle = "•" * (len(compact) - 8)
    masked = compact[:4] + middle + compact[-4:]
    return " ".join(masked[i:i + 4] for i in range(0, len(masked), 4))


def mask_sensitive(text: str) -> tuple[str, list[dict]]:
    """The text with every valid IBAN, NIR and AVS number masked, and the list of what was masked (masked forms
    only, for the document page)."""
    found: list[dict] = []

    def iban(m: re.Match) -> str:
        raw = m.group(1)
        if not iban_ok(raw):
            return raw
        masked = mask_iban(raw)
        found.append({"kind": "iban", "label": "IBAN", "masked": masked})
        return masked

    def nir(m: re.Match) -> str:
        raw = m.group(1)
        if not nir_ok(raw):
            return raw
        compact = raw.replace(" ", "")
        masked = compact[0] + " •• •• •• ••• ••• " + compact[-2:]
        found.append({"kind": "nir", "label": "N° de sécurité sociale", "masked": masked})
        return masked

    def ahv(m: re.Match) -> str:
        raw = m.group(1)
        digits = re.sub(r"\D", "", raw)
        if not ahv_ok(digits):
            return raw
        masked = "756.••••.••••." + digits[-2:]
        found.append({"kind": "avs", "label": "N° AVS", "masked": masked})
        return masked

    text = _IBAN.sub(iban, text)
    text = _NIR.sub(nir, text)
    text = _AHV.sub(ahv, text)
    unique = list({(f["kind"], f["masked"]): f for f in found}.values())
    return text, unique


# ---------------------------------------------------------------- company names

_LEGAL = re.compile(r"(?<![A-Za-z])(S\.?A\.?S\.?U?|S\.?A\.?R\.?L|SàRL|Sàrl|S\.?A\.?|SCI|SNC|SCP|SELARL|EURL|GmbH|AG|Ltd|LLC|SE|"
                    r"Sarl|Sas|Inc\.?)(?![A-Za-z])")
_LABELLED = re.compile(r"(?im)^[ \t]*(?:employeur|raison sociale|soci[ée]t[ée]|entreprise|arbeitgeber|employer|"
                       r"fournisseur|[ée]metteur)[ \t]*:?[ \t]*([^\n]{2,80})$")
_NOT_NAME = re.compile(r"(?i)bulletin|fiche|d[ée]compte|salaire|facture|siret|siren|tva|ide|che-|page|date|p[ée]riode|"
                       r"adresse|t[ée]l|e-?mail|www\.|@|^\W*$")


_FORMS_UPPER = {"sas", "sasu", "sarl", "sa", "sci", "snc", "scp", "selarl", "eurl", "ag", "se", "llc", "ltd", "gmbh", "sàrl"}


def pretty_name(name: str) -> str:
    """ATELIER MOREL SAS -> Atelier Morel SAS; names already in mixed case are kept."""
    if name.upper() != name:
        return name
    words = []
    for word in name.split(" "):
        bare = word.strip(".,").lower()
        if bare in _FORMS_UPPER:
            words.append("Sàrl" if bare == "sàrl" else ("GmbH" if bare == "gmbh" else word.upper()))
        elif bare in ("de", "du", "des", "la", "le", "les", "et", "d", "l"):
            words.append(word.lower())
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)


def _clean_name(line: str) -> str:
    line = re.split(r"\s{3,}|\t|\s[-–|]\s|SIRET|SIREN|N°\s*TVA|TVA|IDE|CHE-|RCS|Capital", line, maxsplit=1)[0]
    line = line.strip(" :,;-–|.")
    return pretty_name(re.sub(r"\s+", " ", line))


def company_near(text: str, pos: int) -> str | None:
    """The company name written near position `pos`: a labelled one (Employeur : …) first, then a line with a legal
    form (SAS, SA, GmbH…) on the same line or up to four lines above, then an upper-case line just above."""
    lines_before = text[max(0, pos - 600):pos].split("\n")
    current = lines_before[-1] + text[pos:pos + 120].split("\n", 1)[0]
    window = lines_before[-5:-1][::-1]
    for line in [current] + window:
        m = _LEGAL.search(line)
        if m:
            name = _clean_name(line)
            if len(name) >= 3 and _LEGAL.search(name) and not re.search(r"(?i)siret|siren|tva|che-", name):
                return name
    for line in window:
        name = _clean_name(line)
        letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", name)
        raw = re.sub(r"\s+", " ", line.strip(" :,;-–|."))
        if len(letters) >= 4 and raw.upper() == raw and not _NOT_NAME.search(name):
            return name
    return None


def labelled_company(text: str) -> tuple[str, int] | None:
    for m in _LABELLED.finditer(text[:20_000]):
        name = _clean_name(m.group(1))
        if len(re.sub(r"[^A-Za-zÀ-ÿ]", "", name)) >= 3:
            return name, m.start()
    return None


def first_company(text: str) -> tuple[str, int] | None:
    """A legal-form line in the document's head (letterheads), for documents without any identifier."""
    head = text[:3000]
    for m in re.finditer(r"[^\n]+", head):
        line = m.group(0)
        if _LEGAL.search(line):
            name = _clean_name(line)
            if 3 <= len(name) <= 60 and _LEGAL.search(name) and not _NOT_NAME.search(name):
                return name, m.start()
    return None


def name_value(name: str) -> str:
    """Two spellings of one company compare equal: no accents, no punctuation, no legal form."""
    value = fold(name)
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    value = re.sub(r"\b(sas|sasu|sarl|sa|sci|snc|scp|selarl|eurl|gmbh|ag|ltd|llc|se|inc)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()


# ---------------------------------------------------------------- the extractors

_SIRET = re.compile(r"(?<![\d])(\d{3}[  .]?\d{3}[  .]?\d{3}[  .]?\d{5})(?![\d])")
_SIREN = re.compile(r"(?i)(?:siren|rcs[^\n\d]{0,30}|n°\s*rcs)\s*:?\s*(\d{3}[  .]?\d{3}[  .]?\d{3})(?![\d])")
_CHE = re.compile(r"(?<![A-Za-z])CHE[\s\-–]?(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})(?!\d)")
_TVA = re.compile(r"(?<![A-Za-z])FR[\s]?([0-9A-HJ-NP-Z]{2})[\s]?(\d{3}[\s]?\d{3}[\s]?\d{3})(?!\d)")
_CADASTRE = re.compile(
    r"(?i)(?:section|cadastr[ée]e?s?(?:\s+(?:section|sous))?|parcelles?|r[ée]f[ée]rences?\s+cadastrales?)\s*:?\s*"
    r"(?:section\s*)?([A-Z]{1,2})\s*(?:n[°o]\.?|num[ée]ro|,)?\s*(\d{1,4})(?!\d)")
_CH_PARCEL = re.compile(r"(?i)(?:bien-fonds|parcelle|feuillet)\s*(?:n[°o]\.?|no)?\s*(\d{2,6})(?!\d)")
_VOIE = (r"rue|chemin|ch\.|route|rte|impasse|all[ée]e|avenue|av\.|boulevard|bd|place|quai|cours|square|passage|"
         r"lotissement|lieu-dit|r[ée]sidence|sentier|mont[ée]e|promenade|esplanade|rampe|ruelle|voie|clos|hameau")
_ADDRESS = re.compile(
    rf"(?i)(?<![\w])(\d{{1,4}}(?:\s?(?:bis|ter|[a-d]))?)\s*,?\s+({_VOIE})\s+([^\n,;]{{2,60}}?)\s*[,\n–-]?\s*"
    r"(?:(?:CH|F)[- ]?)?(\d{5}|\d{4})\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\- ]{1,40}?)(?=\s*(?:\n|,|$|\s{2}|\.|;|\(|cedex))")
_PROPERTY_CONTEXT = re.compile(r"sis|situ[ée]|bien|d[ée]signation|immeuble|logement|lot|propri[ée]t[ée]|"
                               r"maison|appartement|terrain|adresse du|objet|local|habitation|assur[ée]|risque")
_ENTITY_CONTEXT = re.compile(r"notaire|ma[iî]tre|me |[ée]tude|scp|office|agence|banque|cabinet|si[eè]ge|"
                             r"t[ée]l|fax|sas|sarl|soci[ée]t[ée]|employeur|exp[ée]diteur|retour")
_CONTRACT = re.compile(
    r"(?i)(?<![\w])(contrat|police|pr[eê]t|r[ée]f[ée]rence client|r[ée]f\.? client|n°\s*client|num[ée]ro (?:de )?client|"
    r"n° d'adh[ée]rent|adh[ée]rent)\s*(?:n[°o]\.?|num[ée]ro|nr\.?)?\s*:?\s*([A-Z0-9][A-Z0-9\-/.]{3,24}[A-Z0-9])")

_VOIE_NORMAL = {"ch.": "chemin", "rte": "route", "av.": "avenue", "bd": "boulevard", "allee": "allee"}


def _address_value(number: str, voie: str, name: str, postcode: str, city: str) -> str:
    voie = fold(voie)
    voie = _VOIE_NORMAL.get(voie, voie)
    name = re.sub(r"\bst\b", "saint", fold(name))
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    city = re.sub(r"[^a-z0-9 ]", " ", fold(city))
    value = f"{fold(number).replace(' ', '')} {voie} {name} {postcode} {city}"
    return re.sub(r"\s+", " ", value).strip()


def _address_label(number: str, voie: str, name: str, postcode: str, city: str) -> str:
    voie_l = voie.lower()
    voie_l = {"ch.": "chemin", "rte": "route", "av.": "avenue", "bd": "boulevard"}.get(voie_l, voie_l)
    name = re.sub(r"\s+", " ", name.strip())
    city = city.strip()
    city = city if city.lower() != city else city.title()
    if city.upper() == city:
        city = city.title()
    return f"{number} {voie_l} {name}, {postcode} {city}"


def addresses(text: str) -> list[tuple[Key, float]]:
    """Every postal address, with a score: positive when written as the property's (sis, désignation…),
    negative when it is a notary's, a bank's or a company's own address."""
    out = []
    for m in _ADDRESS.finditer(text[:200_000]):
        number, voie, name, postcode, city = (g or "" for g in m.groups())
        if re.search(r"\d{3,}", name):
            continue
        value = _address_value(number, voie, name, postcode, city)
        label = _address_label(number, voie, name, postcode, city)
        before = fold(text[max(0, m.start() - 160):m.start()])
        score = 0.0
        if _PROPERTY_CONTEXT.search(before[-120:]):
            score += 2
        if _ENTITY_CONTEXT.search(before.rsplit("\n", 1)[-1][-80:]):  # same line: a notary's or a company's own address
            score -= 2
        out.append((Key("property_address", value, label, m.start()), score))
    return out


def _nearest_address_label(found: list, pos: int) -> str | None:
    """The property's address written near `pos`: best score first (a notary's own address scores low), then the
    closest. None when the only addresses nearby are entities' own."""
    near = [(a, s) for a, s in found if abs(a.position - pos) < 400]
    if not near:
        return None
    best = max(near, key=lambda c: (c[1], -abs(c[0].position - pos)))
    return best[0].label if best[1] >= 0 else None


def extract(text: str, doc_year: int | None = None) -> list[Key]:
    """Every key of the document, best first within each kind."""
    keys: list[Key] = []
    seen: set[tuple[str, str]] = set()

    def add(key: Key) -> None:
        if (key.key, key.value) not in seen:
            seen.add((key.key, key.value))
            keys.append(key)

    body = text[:200_000]
    company_labels: list[tuple[str, int]] = []

    for m in _SIRET.finditer(body):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 14 and luhn_ok(digits) and digits != "0" * 14:
            name = company_near(body, m.start())
            label = name or f"SIRET {digits[:3]} {digits[3:6]} {digits[6:9]} {digits[9:]}"
            add(Key("siret", digits, label, m.start()))
            add(Key("siren", digits[:9], label, m.start()))
            if name:
                company_labels.append((name, m.start()))
    for m in _SIREN.finditer(body):
        digits = re.sub(r"\D", "", m.group(1))
        if luhn_ok(digits):
            name = company_near(body, m.start())
            add(Key("siren", digits, name or f"SIREN {digits[:3]} {digits[3:6]} {digits[6:]}", m.start()))
            if name:
                company_labels.append((name, m.start()))
    for m in _CHE.finditer(body):
        digits = "".join(m.groups())
        if che_ok(digits):
            name = company_near(body, m.start())
            add(Key("che_uid", digits, name or f"CHE-{digits[:3]}.{digits[3:6]}.{digits[6:]}", m.start()))
            if name:
                company_labels.append((name, m.start()))
    for m in _TVA.finditer(body):
        tkey, siren = m.group(1), re.sub(r"\D", "", m.group(2))
        if luhn_ok(siren) and tva_fr_ok(tkey, siren):
            name = company_near(body, m.start())
            add(Key("tva", f"FR{tkey}{siren}", name or f"TVA FR{tkey} {siren}", m.start()))

    labelled = labelled_company(body)
    if labelled:
        add(Key("employer_name", name_value(labelled[0]), labelled[0], labelled[1]))
    elif company_labels:
        add(Key("employer_name", name_value(company_labels[0][0]), company_labels[0][0], company_labels[0][1]))
    for name, pos in ([labelled] if labelled else []) + company_labels + ([first_company(body)] if first_company(body) else []):
        if name_value(name):
            add(Key("company", name_value(name), name, pos))

    found_addresses = addresses(body)
    for m in _CADASTRE.finditer(body):
        section, number = m.group(1).upper(), str(int(m.group(2)))
        if section in ("N", "NO"):
            continue
        label = _nearest_address_label(found_addresses, m.start()) or f"Section {section} n° {number}"
        add(Key("cadastre_ref", f"{section}-{number}", label, m.start()))
    for m in _CH_PARCEL.finditer(body):
        add(Key("cadastre_ref", f"parcelle-{int(m.group(1))}",
                _nearest_address_label(found_addresses, m.start()) or f"Parcelle {m.group(1)}", m.start()))
    # the property's address first; a notary's or a bank's own address only when nothing better is written
    ranked = sorted(found_addresses, key=lambda a: (-a[1], a[0].position))
    best = ranked[0][1] if ranked else 0
    for address, score in ranked:
        if score < 0 and best > 0:
            continue
        add(address)

    for m in _CONTRACT.finditer(body):
        number = m.group(2).strip(".-/")
        if not re.search(r"\d", number) or len(number) < 4:
            continue
        kind = fold(m.group(1))
        word = "Prêt" if kind.startswith("pr") else "Contrat"
        add(Key("contract_no", number.upper(), f"{word} {number.upper()}", m.start()))

    if doc_year:
        add(Key("year", str(doc_year), str(doc_year)))
    return keys
