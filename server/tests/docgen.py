"""Synthetic test documents: payslips (2 French employers, 1 Swiss), property papers for two homes sharing a notary,
invoices, a noisy scan, a DOCX, protected PDFs."""
from __future__ import annotations

import random
import zipfile
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

LAST = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def pdf(path: Path, lines: list[str], user_password: str | None = None, owner_only: bool = False) -> Path:
    from reportlab.lib.pdfencrypt import StandardEncryption

    kwargs = {}
    if user_password is not None or owner_only:
        kwargs["encrypt"] = StandardEncryption(userPassword="" if owner_only else user_password, ownerPassword="owner-secret",
                                               canPrint=0, canModify=0, canCopy=0)
    c = canvas.Canvas(str(path), pagesize=A4, **kwargs)
    y = 800
    for line in lines:
        c.setFont("Helvetica", 10)
        c.drawString(50, y, line)
        y -= 15
    c.showPage()
    c.save()
    return path


def payslip_fr(path, employer, siret, month, year, password=None):
    d = LAST[month - 1]
    return pdf(path, [employer.upper(), "8 rue de la Paix, 75002 Paris", f"SIRET : {siret}", "", "BULLETIN DE SALAIRE",
                      f"Période du 01/{month:02d}/{year} au {d:02d}/{month:02d}/{year}", f"Date de paiement : {d:02d}/{month:02d}/{year}",
                      "Salaire de base 3 800,00", "Salaire brut 4 000,00", "Cotisations salariales 880,00", "Net à payer 3 120,00"], password)


def payslip_ch(path, employer, uid, month, year):
    d = LAST[month - 1]
    return pdf(path, [employer.upper(), "Rue du Lac 12, 1003 Lausanne", f"IDE : {uid}", "", "DÉCOMPTE DE SALAIRE",
                      f"Période : {d:02d}.{month:02d}.{year}", "Salaire brut 7 000.00", "Cotisations AVS/AI/APG 371.00",
                      "Cotisation LPP 480.00", "Salaire net 5 600.00", "Net à payer 5 600.00"])


NOTARY = "Maître Claire Durand, notaire, 3 place de la Mairie, 74000 Annecy"


def immo(path, kind, street, city, cadastre, date):
    body = {
        "acte": ["ACTE DE VENTE", f"Fait à Annecy, le {date}", NOTARY, f"DÉSIGNATION DU BIEN : maison sise {street}, {city}",
                 f"Cadastrée section {cadastre}", "Le vendeur et l'acquéreur ont comparu devant le notaire."],
        "compromis": ["COMPROMIS DE VENTE", f"Fait le {date}", NOTARY, f"Bien situé {street}, {city}", f"Cadastre : section {cadastre}",
                      "Promesse de vente entre vendeur et acquéreur."],
        "pret": ["OFFRE DE PRÊT IMMOBILIER", f"Émis le {date}", "Banque Alpine", "Prêt n° PR-2026-884411",
                 f"Bien financé : {street}, {city}", "Offre de prêt acquéreur."],
    }[kind]
    return pdf(path, body)


def invoice(path, company, siret, number, date, energy=False):
    lines = [company.upper(), "Rue de l'Industrie 5, 69003 Lyon", f"SIRET : {siret}", "", f"FACTURE n° {number}", f"Date de facture : {date}", ""]
    lines += (["Électricité : consommation 412 kWh", "Point de livraison 1234", "Abonnement 12,00"] if energy
              else ["Prestation de service", "Montant HT 100,00", "TVA 20,00", "Total TTC 120,00"])
    return pdf(path, lines)


def noisy_scan(path, lines, seed=1):
    from PIL import Image, ImageDraw, ImageFont

    rng = random.Random(seed)
    img = Image.new("L", (1240, 1754), 245)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    y = 120
    for line in lines:
        draw.text((100, y), line, fill=20, font=font)
        y += 58
    px = img.load()
    for _ in range(9000):
        px[rng.randrange(img.width), rng.randrange(img.height)] = rng.randrange(120, 255)
    img.rotate(0.6, fillcolor=245).save(path)
    return path


def docx(path, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("word/document.xml", xml)
    return path
