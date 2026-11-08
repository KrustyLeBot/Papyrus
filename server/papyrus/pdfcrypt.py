"""Password-protected PDFs, with pikepdf (qpdf).

An owner password alone (printing or copying restricted, opening free) is removed silently at import. A user
password (the file cannot be opened) parks the document until someone types the password; the decrypted copy is
then kept. The password itself only lives in this function's arguments: it is never stored, logged or returned."""
from __future__ import annotations

from pathlib import Path

PLAIN, OWNER_ONLY, USER_PASSWORD, BROKEN = "plain", "owner", "user", "broken"


def inspect(path: Path) -> str:
    import pikepdf

    try:
        with pikepdf.open(path) as pdf:
            return OWNER_ONLY if pdf.is_encrypted else PLAIN
    except pikepdf.PasswordError:
        return USER_PASSWORD
    except Exception:  # noqa: BLE001 - qpdf could not read it; extraction will report the error
        return BROKEN


def decrypt(src: Path, dst: Path, password: str = "") -> bool:
    """Writes a PDF without any password or restriction; False when the password is wrong."""
    import pikepdf

    try:
        pdf = pikepdf.open(src, password=password)
    except pikepdf.PasswordError:
        return False
    try:
        tmp = dst.with_name(dst.name + ".part")
        pdf.save(tmp, encryption=False)
    finally:
        pdf.close()
    tmp.replace(dst)
    return True
