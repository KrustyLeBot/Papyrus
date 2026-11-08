"""Command line: `papyrus` (serve), `papyrus adduser <name>`, `papyrus backup`, `papyrus export`, `papyrus scan`, `papyrus reindex`,
`papyrus check`."""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path


def serve() -> int:
    import uvicorn

    from .app import create_app
    from .config import Config
    from .htpasswd import Accounts

    config = Config.from_env()
    config.ensure_dirs()
    if not config.auth_disabled:
        accounts = Accounts(config.htpasswd_path)
        if not accounts.users:
            print("\npapyrus: no usable account, refusing to start.\n"
                  f"  {config.htpasswd_path} is missing, empty or only holds weak hashes.\n"
                  "  Create an account (bcrypt):\n"
                  "      docker compose run --rm papyrus adduser <name>\n"
                  "  or:  htpasswd -B -c conf/httpasswd <name>\n"
                  "  Behind Cloudflare Access only, you may set AUTH_DISABLED=true instead.\n", file=sys.stderr)
            for problem in accounts.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
    else:
        print("papyrus: AUTH_DISABLED=true, anyone who reaches the port is logged in. Only use it behind an access proxy.", flush=True)
    if not config.source_dir.is_dir():
        print(f"papyrus: the source folder {config.source_dir} is not there: mount it (read-only is enough) on /source.", flush=True)
    app = create_app(config)
    uvicorn.run(app, host="0.0.0.0", port=config.port, log_level="warning", proxy_headers=False, access_log=False,
                timeout_keep_alive=30)
    return 0


def adduser(args: list[str]) -> int:
    from .config import Config
    from .htpasswd import hash_password, parse

    if len(args) != 1 or ":" in args[0] or not args[0].strip():
        print("usage: papyrus adduser <name>", file=sys.stderr)
        return 2
    name = args[0].strip()
    path = Config.from_env().htpasswd_path
    if not sys.stdin.isatty():
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Mot de passe : ")
        if getpass.getpass("Confirmer : ") != password:
            print("Les mots de passe ne correspondent pas.", file=sys.stderr)
            return 1
    if len(password) < 12:
        print("Le mot de passe doit faire 12 caractères au moins.", file=sys.stderr)
        return 1
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    except OSError as error:
        print(f"{path} : {error}. Le dossier conf est-il monté en lecture seule ? Lance la commande avec "
              f"`-v ./conf:/conf` (sans :ro).", file=sys.stderr)
        return 1
    lines = [ln for ln in lines if ln.partition(":")[0] != name]
    lines.append(f"{name}:{hash_password(password)}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError as error:
        print(f"{path} : {error}. Le dossier conf est-il monté en lecture seule ?", file=sys.stderr)
        return 1
    users, problems = parse("\n".join(lines))
    print(f"Compte « {name} » enregistré dans {path} ({len(users)} compte(s)).")
    return 0


def _services():
    from .config import Config
    from .services import Services

    config = Config.from_env()
    return Services(config)


def backup() -> int:
    import datetime as dt

    svc = _services()
    target = svc.config.backups_dir / f"papyrus-{dt.datetime.now():%Y-%m-%d-%H%M%S}.db"
    svc.db.backup_to(target)
    print(f"Snapshot cohérent de la base : {target}")
    svc.close()
    return 0


def export() -> int:
    svc = _services()
    c = svc.db.conn
    out = {
        "format": "papyrus-export-2",
        "documents": [dict(r) for r in c.execute(
            "SELECT id, uuid, title, title_manual, original_name, ext, size, sha256, status, doc_date, date_source, types, "
            "summary, pages, ocr, unlocked_at, created_at FROM documents")],
        "locations": [dict(r) for r in c.execute("SELECT doc_id, path, folder FROM locations")],
        "tags": [dict(r) for r in c.execute("SELECT id, name FROM tags")],
        "doc_tags": [dict(r) for r in c.execute("SELECT doc_id, tag_id, source FROM doc_tags")],
        "keys": [dict(r) for r in c.execute("SELECT doc_id, key, value, label FROM doc_keys")],
        "ignored": [dict(r) for r in c.execute("SELECT sha256, title, path FROM ignored")],
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    svc.close()
    return 0


def scan() -> int:
    """One scan of the source folder, then the analysis of what it found (no server)."""
    svc = _services()
    result = svc.scanner.scan()
    print(f"Analyse du dossier : {result}")
    while svc.worker.run_one():
        pass
    if svc.scanner.state.get("warning"):
        print(svc.scanner.state["warning"], file=sys.stderr)
    svc.close()
    return 0


def reindex() -> int:
    from .pipeline import reembed_all

    svc = _services()
    if not svc.embedder.available and not svc.embedder.download():
        print("Modèle d'embeddings indisponible.", file=sys.stderr)
        return 1
    reembed_all(svc.processor, svc.db)
    print("Embeddings recalculés.")
    svc.close()
    return 0


def check() -> int:
    """Self-test of the image: the tools and libraries the app needs."""
    import shutil

    ok = True
    for tool in ("tesseract", "pdftotext", "pdftoppm", "pdfinfo"):
        found = shutil.which(tool)
        print(f"{'ok ' if found else 'MISSING'} {tool}")
        ok &= bool(found)
    import onnxruntime
    import pikepdf
    import tokenizers
    print(f"ok  onnxruntime {onnxruntime.__version__}, pikepdf {pikepdf.__version__}, tokenizers {tokenizers.__version__}")
    return 0 if ok else 1


def main() -> int:
    argv = sys.argv[1:]
    command = argv[0] if argv else "serve"
    if command in ("serve", "run"):
        return serve()
    if command == "adduser":
        return adduser(argv[1:])
    if command == "backup":
        return backup()
    if command == "export":
        return export()
    if command == "scan":
        return scan()
    if command == "reindex":
        return reindex()
    if command == "check":
        return check()
    print(f"papyrus: unknown command {command!r} (serve, adduser, backup, export, scan, reindex, check)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
