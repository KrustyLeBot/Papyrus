"""The HTTP API (/api/…), the health check and the single-page UI, in one FastAPI application."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__, identifiers
from .auth import CSRF_COOKIE, SESSION_COOKIE, Auth, client_ip, cookie_secure
from .config import Config
from .pipeline import add_tag, index_document, prune_tags, reembed_all
from .services import Services

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' "
       "'unsafe-inline'; font-src 'self'; connect-src 'self'; frame-src 'self' blob:; object-src 'self'; "
       "frame-ancestors 'self'; base-uri 'self'; form-action 'self'")


# ---------------------------------------------------------------------------------------------- request bodies

class LoginBody(BaseModel):
    user: str = Field(max_length=200)
    password: str = Field(max_length=1000)


class UnlockBody(BaseModel):
    password: str = Field(max_length=1000)
    try_others: bool = True
    remember: bool = True  # keep it (encrypted, by the document's hash) for the next protected PDFs


class DocPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    doc_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    hidden: bool | None = None


class TagBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class SearchSettingsBody(BaseModel):
    semantic_min: float = Field(ge=0.5, le=0.95)
    prefix: bool = True
    fuzzy: bool = True


# ---------------------------------------------------------------------------------------------- helpers

def _err(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _rss_mb() -> float | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def create_app(config: Config | None = None, services: Services | None = None, start_worker: bool = True) -> FastAPI:
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.services.close()

    app = FastAPI(title="Papyrus", version=__version__, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    svc = services or Services(config, start_worker=start_worker)
    app.state.services = svc
    auth = Auth(config, svc.db, svc.accounts)
    app.state.auth = auth

    def basic_login(name: str, password: str) -> None:
        fingerprint = svc.accounts.fingerprint(name)
        if not svc.vault.is_unlocked(name, fingerprint):
            svc.vault.unlock(name, password, fingerprint)

    auth.on_basic_login = basic_login

    def retry_waiting_pdfs() -> None:
        """With the key back, protected PDFs that waited get tried against the saved passwords (they go back to waiting
        if none fits)."""
        if svc.vault.count():
            with db.tx():
                n = db.conn.execute("UPDATE documents SET status = 'pending' WHERE status = 'password_required'").rowcount
            if n:
                svc.worker.wake()

    if start_worker and not svc.embedder.available:
        threading.Thread(target=svc.embedder.download, name="model-download", daemon=True).start()

    def _vectors_janitor():
        while True:
            time.sleep(60)
            svc.vectors.drop_if_idle()

    threading.Thread(target=_vectors_janitor, name="vectors-janitor", daemon=True).start()

    # ------------------------------------------------------------------------------------------ middleware

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        # never echo the submitted values: a password could be among them
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "corps" for e in exc.errors()})
        return JSONResponse({"detail": f"Requête invalide ({', '.join(fields)})."}, status_code=422)

    def user(request: Request) -> str:
        name, how = auth.authenticate(request)
        if not name:
            ip = client_ip(request, config)
            wait = auth.throttle.retry_after(ip)
            if wait:
                raise HTTPException(429, f"Trop de tentatives, réessaie dans {wait // 60 + 1} min.",
                                    headers={"Retry-After": str(wait)})
            headers = {"WWW-Authenticate": 'Basic realm="Papyrus"'} if how == "basic-failed" else None
            raise HTTPException(401, "Connexion requise.", headers=headers)
        if how == "session" and request.method not in SAFE_METHODS and not auth.csrf_ok(request):
            raise HTTPException(403, "Jeton CSRF manquant ou invalide, recharge la page.")
        request.state.user = name
        return name

    db = svc.db

    # ------------------------------------------------------------------------------------------ health & auth

    @app.get("/health", include_in_schema=False)
    def health():
        db.conn.execute("SELECT 1").fetchone()
        return {"ok": True}

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request):
        if auth.disabled:
            return {"user": "local"}
        ip = client_ip(request, config)
        wait = auth.throttle.retry_after(ip)
        if wait:
            raise HTTPException(429, f"Trop de tentatives, réessaie dans {wait // 60 + 1} min.",
                                headers={"Retry-After": str(wait)})
        if not svc.accounts.verify(body.user, body.password):
            auth.throttle.failed(ip)
            print(f"papyrus: failed login from {ip}", flush=True)
            raise _err(401, "Identifiant ou mot de passe incorrect.")
        auth.throttle.succeeded(ip)
        svc.vault.unlock(body.user, body.password, svc.accounts.fingerprint(body.user))  # the saved PDF passwords open
        retry_waiting_pdfs()
        auth.close_session(request.cookies.get(SESSION_COOKIE))
        token = auth.open_session(body.user)
        response = JSONResponse({"user": body.user})
        secure = cookie_secure(request, config)
        age = config.session_days * 86400
        response.set_cookie(SESSION_COOKIE, token, max_age=age, path="/", httponly=True, secure=secure, samesite="lax")
        response.set_cookie(CSRF_COOKIE, uuid.uuid4().hex, max_age=age, path="/", httponly=False, secure=secure,
                            samesite="lax")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        auth.close_session(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @app.get("/api/auth/me")
    def me(request: Request, name: str = Depends(user)):
        response = JSONResponse({"user": name, "auth_disabled": auth.disabled, "build": config.build,
                                 "version": __version__})
        if auth.disabled and not request.cookies.get(CSRF_COOKIE):
            response.set_cookie(CSRF_COOKIE, uuid.uuid4().hex, path="/", httponly=False, samesite="lax")
        return response

    # ------------------------------------------------------------------------------------------ serialisers

    def serialize(rows, full: bool = False) -> list[dict]:
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        places: dict[int, list] = {i: [] for i in ids}
        for r in db.conn.execute(f"SELECT doc_id, path, folder FROM locations WHERE doc_id IN ({marks}) ORDER BY id", ids):
            places[r["doc_id"]].append({"path": r["path"], "folder": r["folder"]})
        tags: dict[int, list] = {i: [] for i in ids}
        for r in db.conn.execute(f"SELECT dt.doc_id, t.id, t.name, dt.source FROM doc_tags dt JOIN tags t ON "
                                 f"t.id = dt.tag_id WHERE dt.doc_id IN ({marks}) ORDER BY t.name", ids):
            tags[r["doc_id"]].append({"id": r["id"], "name": r["name"], "source": r["source"]})
        out = []
        for r in rows:
            where = places[r["id"]]
            item = {
                "id": r["id"], "title": r["title"], "date": r["doc_date"], "date_source": r["date_source"],
                "created_at": r["created_at"], "status": r["status"], "ext": r["ext"], "pages": r["pages"],
                "size": r["size"], "original_name": r["original_name"], "error": r["error"], "tags": tags[r["id"]],
                "types": json.loads(r["types"] or "[]"),
                "thumb": f"/api/documents/{r['id']}/thumb?v={quote(r['updated_at'])}" if r["status"] == "done" else None,
                "path": where[0]["path"] if where else "", "folder": where[0]["folder"] if where else "",
                "copies": len(where), "summary": r["summary"] if full else (r["summary"] or "")[:200],
                "hidden": bool(r["hidden"]),
            }
            if full:
                item.update({
                    "text": r["text"], "ocr": bool(r["ocr"]), "unlocked_at": r["unlocked_at"],
                    "processed_at": r["processed_at"], "title_manual": bool(r["title_manual"]),
                    "has_embedding": r["embedding"] is not None, "locations": where,
                })
            out.append(item)
        return out

    def get_doc(doc_id: int):
        row = db.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            raise _err(404, "Document introuvable.")
        return row

    def touch(doc_id: int) -> None:
        db.conn.execute("UPDATE documents SET updated_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now') WHERE id = ?", (doc_id,))

    HIDDEN = "('password_required', 'missing')"

    # ------------------------------------------------------------------------------------------ documents

    @app.get("/api/documents")
    def list_documents(_u: str = Depends(user), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                       folder: str | None = None, children: bool = True, tag: str | None = None,
                       type: str | None = None, status: str | None = None,
                       date_from: str | None = None, date_to: str | None = None, hidden: bool = False):
        clauses, params = ["hidden = ?"], [int(hidden)]  # the timeline never shows hidden documents; « Masqués » only does
        if status == "all":
            pass
        elif status:
            clauses.append("status = ?")
            params.append(status)
        else:
            clauses.append(f"status NOT IN {HIDDEN}")
        if folder == ".":  # the files lying at the root of the source folder
            clauses.append("id IN (SELECT doc_id FROM locations WHERE folder = '')")
        elif folder is not None and folder != "":
            like = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
            if children:
                clauses.append("id IN (SELECT doc_id FROM locations WHERE folder = ? OR folder LIKE ? ESCAPE '\\')")
                params += [folder, like]
            else:
                clauses.append("id IN (SELECT doc_id FROM locations WHERE folder = ?)")
                params.append(folder)
        if tag:
            clauses.append("id IN (SELECT dt.doc_id FROM doc_tags dt JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)")
            params.append(tag)
        if type:
            clauses.append("EXISTS (SELECT 1 FROM json_each(documents.types) WHERE value = ?)")
            params.append(type)
        if date_from:
            clauses.append("doc_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("doc_date <= ?")
            params.append(date_to)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        total = db.conn.execute(f"SELECT COUNT(*) FROM documents {where}", params).fetchone()[0]
        rows = db.conn.execute(
            f"SELECT id, title, doc_date, date_source, created_at, updated_at, status, ext, pages, size, original_name, "
            f"error, types, summary, hidden FROM documents {where} "
            f"ORDER BY COALESCE(doc_date, substr(created_at, 1, 10)) DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        return {"items": serialize(rows), "total": total, "offset": offset, "limit": limit}

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: int, _u: str = Depends(user)):
        row = get_doc(doc_id)
        item = serialize([row], full=True)[0]
        item["keys"] = [dict(k) for k in db.conn.execute("SELECT key, value, label FROM doc_keys WHERE doc_id = ? ORDER BY rowid",
                                                         (doc_id,))]
        # the stored text is already masked: what was masked is read back from the bullets left in it
        item["sensitive"] = [{"label": "IBAN", "masked": m.group(0)} for m in
                             re.finditer(r"[A-Z]{2}\d{2}(?: ?[A-Z0-9•]{1,4}){3,8}", row["text"]) if "•" in m.group(0)][:5]
        return item

    @app.patch("/api/documents/{doc_id}")
    def patch_document(doc_id: int, body: DocPatch, _u: str = Depends(user)):
        row = get_doc(doc_id)
        with db.tx():
            if body.title is not None:
                title = " ".join(body.title.split()) or row["title"]
                db.conn.execute("UPDATE documents SET title = ?, title_manual = 1 WHERE id = ?", (title, doc_id))
            if body.doc_date is not None:
                db.conn.execute("UPDATE documents SET doc_date = ?, date_source = 'manual' WHERE id = ?", (body.doc_date, doc_id))
            touch(doc_id)
        if body.hidden is not None:
            svc.library.set_hidden(doc_id, body.hidden)
        with db.tx():
            if row["status"] == "done" and not body.hidden:
                if body.doc_date is not None:
                    svc.processor.auto_tags(doc_id)
                else:
                    index_document(db, doc_id)
        return document(doc_id, _u)

    @app.delete("/api/documents/{doc_id}")
    def forget_document(doc_id: int, _u: str = Depends(user)):
        """« Retirer de Papyrus »: the analysis is deleted and the content ignored by later scans; the file stays."""
        get_doc(doc_id)
        svc.library.forget(doc_id)
        return {"ok": True}

    @app.post("/api/documents/{doc_id}/reprocess")
    def reprocess(doc_id: int, _u: str = Depends(user)):
        row = get_doc(doc_id)
        if row["status"] in ("password_required", "missing"):
            raise _err(409, "Ce document n'est pas analysable pour l'instant (mot de passe ou fichier introuvable).")
        with db.tx():
            db.conn.execute("UPDATE documents SET status = 'pending', error = NULL WHERE id = ?", (doc_id,))
        svc.worker.wake()
        return {"ok": True}

    @app.get("/api/documents/{doc_id}/file")
    def document_file(doc_id: int, download: bool = False, _u: str = Depends(user)):
        row = get_doc(doc_id)
        if row["status"] == "password_required":
            raise _err(409, "Ce PDF est encore protégé : déverrouille-le d'abord.")
        path = svc.processor.path_of(row)
        if path is None:
            raise _err(404, "Le fichier n'est plus dans le dossier analysé.")
        disposition = "attachment" if download else "inline"
        name = row["original_name"]
        ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name)
        headers = {"Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
                   "Cache-Control": "private, max-age=300"}
        if row["ext"] != "pdf":
            # a document is never a page of this site: no script runs from it
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        return FileResponse(path, media_type=row["mime"] or "application/octet-stream", headers=headers)

    @app.get("/api/documents/{doc_id}/thumb")
    def document_thumb(doc_id: int, _u: str = Depends(user)):
        row = get_doc(doc_id)
        path = config.thumbs_dir / f"{row['uuid']}.webp"
        if not path.is_file():
            raise _err(404, "Pas de vignette.")
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/documents/{doc_id}/similar")
    def similar(doc_id: int, limit: int = Query(10, ge=1, le=50), _u: str = Depends(user)):
        get_doc(doc_id)
        found = svc.searcher.similar(doc_id, limit)
        if not found:
            return {"items": []}
        ids = [d for d, _ in found]
        rows = {r["id"]: r for r in db.conn.execute(
            f"SELECT * FROM documents WHERE id IN ({','.join('?' * len(ids))})", ids)}
        items = serialize([rows[d] for d in ids if d in rows])
        scores = dict(found)
        for item in items:
            item["score"] = round(scores[item["id"]], 3)
        return {"items": items}

    @app.post("/api/documents/{doc_id}/tags")
    def add_document_tag(doc_id: int, body: TagBody, _u: str = Depends(user)):
        get_doc(doc_id)
        with db.tx():
            add_tag(db, doc_id, body.name.strip(), "manual")
            touch(doc_id)
            index_document(db, doc_id)
        return document(doc_id, _u)

    @app.delete("/api/documents/{doc_id}/tags/{tag_id}")
    def remove_document_tag(doc_id: int, tag_id: int, _u: str = Depends(user)):
        get_doc(doc_id)
        with db.tx():
            db.conn.execute("DELETE FROM doc_tags WHERE doc_id = ? AND tag_id = ?", (doc_id, tag_id))
            prune_tags(db)
            touch(doc_id)
            index_document(db, doc_id)
        return document(doc_id, _u)

    # ------------------------------------------------------------------------------------------ protected PDFs

    @app.get("/api/locked")
    def locked(_u: str = Depends(user)):
        return {"items": svc.library.locked()}

    @app.post("/api/documents/{doc_id}/unlock")
    def unlock(doc_id: int, body: UnlockBody, request: Request, _u: str = Depends(user)):
        first = svc.library.unlock(doc_id, body.password, body.remember, request.state.user)
        if first.status == "rejected":
            raise _err(409, first.message)
        if first.status == "password_required":
            raise _err(400, "Mot de passe incorrect. Le document reste en attente.")
        others = []
        if body.try_others:
            for item in svc.library.locked():
                result = svc.library.unlock(item["id"], body.password, body.remember, request.state.user)
                if result.status == "queued":
                    others.append({"id": result.doc_id, "name": result.name})
        svc.worker.wake()
        return {"status": first.status, "id": first.doc_id, "others": others, "remaining": len(svc.library.locked()),
                "remembered": first.remembered, "asked_to_remember": body.remember}

    # ------------------------------------------------------------------------------------------ folders & scan

    @app.get("/api/folders")
    def folders(_u: str = Depends(user)):
        rows = db.conn.execute(
            f"SELECT l.folder, COUNT(DISTINCT l.doc_id) AS n FROM locations l JOIN documents d ON d.id = l.doc_id "
            f"WHERE d.status NOT IN {HIDDEN} AND d.hidden = 0 GROUP BY l.folder ORDER BY l.folder").fetchall()
        return {"items": [{"path": r["folder"], "count": r["n"]} for r in rows]}

    def scan_info() -> dict:
        st = dict(svc.scanner.state)
        st["source"] = str(config.source_dir)
        st["interval"] = config.scan_interval
        st["extensions"] = sorted(config.extensions)
        return st

    @app.get("/api/scan")
    def scan_status(_u: str = Depends(user)):
        return scan_info()

    @app.post("/api/scan")
    def scan_now(_u: str = Depends(user)):
        svc.scanner.scan_now()
        return scan_info()

    # ------------------------------------------------------------------------------------------ search

    @app.get("/api/search")
    def search(q: str = Query("", max_length=500), exact: bool = False, folder: str | None = None, children: bool = True,
               tag: str | None = None, type: str | None = None, date_from: str | None = None,
               date_to: str | None = None, limit: int = Query(50, ge=1, le=200), _u: str = Depends(user)):
        if not q.strip():
            return list_documents(_u, 0, limit, folder, children, tag, type, None, date_from, date_to, False)
        found = svc.searcher.search(q, exact, folder or None, children, tag, type, date_from, date_to, limit)
        ids = found["ids"]
        rows = {r["id"]: r for r in db.conn.execute(
            f"SELECT * FROM documents WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
        items = serialize([rows[d] for d in ids if d in rows])
        for item in items:
            item["snippet"] = found["snippets"].get(item["id"]) or ""
            item["match"] = found["how"].get(item["id"], "")
        return {"items": items, "total": found["total"], "elapsed_ms": found["elapsed_ms"], "semantic": found["semantic"],
                "corrections": found["corrections"]}

    @app.get("/api/tags")
    def tags(_u: str = Depends(user)):
        rows = db.conn.execute("SELECT t.id, t.name, COUNT(dt.doc_id) AS n FROM tags t LEFT JOIN doc_tags dt ON "
                               "dt.tag_id = t.id GROUP BY t.id ORDER BY n DESC, t.name").fetchall()
        return {"items": [{"id": r["id"], "name": r["name"], "count": r["n"]} for r in rows]}

    @app.get("/api/types")
    def types(_u: str = Depends(user)):
        return {"items": [{"name": t.name, "color": t.color} for t in svc.rules.rules.types]}

    # ------------------------------------------------------------------------------------------ queue & admin

    @app.get("/api/queue")
    def queue(_u: str = Depends(user)):
        rows = db.conn.execute(
            "SELECT id, title, original_name, status, error, created_at FROM documents WHERE status IN "
            "('pending', 'processing', 'error') ORDER BY CASE status WHEN 'processing' THEN 0 WHEN 'pending' THEN 1 "
            "ELSE 2 END, id LIMIT 200").fetchall()
        locked_count = db.conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'password_required'").fetchone()[0]
        hidden_count = db.conn.execute(f"SELECT COUNT(*) FROM documents WHERE hidden = 1 AND status NOT IN {HIDDEN}").fetchone()[0]
        return {"items": [dict(r) for r in rows], "current": svc.worker.current, "jobs": svc.worker.jobs(),
                "locked": locked_count, "hidden": hidden_count, "scan": scan_info(),
                "vault_locked": svc.vault.status()["locked"]}

    @app.get("/api/admin/status")
    def admin_status(_u: str = Depends(user)):
        counts = {r["status"]: r["n"] for r in db.conn.execute("SELECT status, COUNT(*) AS n FROM documents GROUP BY status")}
        size = db.conn.execute("SELECT COALESCE(SUM(size), 0) FROM documents WHERE status != 'missing'").fetchone()[0]
        db_size = sum(p.stat().st_size for p in config.data_dir.glob("papyrus.db*") if p.is_file())
        missing = db.conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'done' AND embedding IS NULL").fetchone()[0]
        return {
            "documents": sum(counts.values()), "by_status": counts, "files_bytes": size, "db_bytes": db_size,
            "tags": db.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            "database": {"journal": db.journal, "network": db.network},
            "locations": db.conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0],
            "ignored": db.conn.execute("SELECT COUNT(*) FROM ignored").fetchone()[0],
            "passwords": svc.vault.count(), "vault": svc.vault.status(),
            "scan": scan_info(),
            "worker": {"alive": bool(svc.worker.thread and svc.worker.thread.is_alive()), "current": svc.worker.current,
                       "jobs": svc.worker.jobs()},
            "model": {"status": svc.embedder.status(), "error": svc.embedder.error, "missing_embeddings": missing,
                      "name": svc.embedder.name, "dim": svc.embedder.dim},
            "rules": {"source": svc.rules.rules.source, "types": len(svc.rules.rules.types)},
            "search": svc.searcher.settings(),
            "memory_mb": _rss_mb(), "version": __version__, "build": config.build,
            "auth": {"disabled": auth.disabled, "accounts": len(svc.accounts.users) if svc.accounts else 0,
                     "problems": svc.accounts.problems if svc.accounts else []},
        }

    @app.put("/api/admin/search-settings")
    def put_search_settings(body: SearchSettingsBody, _u: str = Depends(user)):
        return svc.searcher.save_settings(body.semantic_min, body.prefix, body.fuzzy)

    @app.post("/api/admin/reload-rules")
    def reload_rules(_u: str = Depends(user)):
        try:
            rules = svc.rules.reload()
        except Exception as error:  # noqa: BLE001 - the message names the problem in the file
            raise _err(400, f"Règles non rechargées : {error}") from None
        return {"ok": True, "source": rules.source, "types": len(rules.types)}

    @app.post("/api/admin/reindex")
    def reindex(_u: str = Depends(user)):
        if not svc.embedder.available:
            raise _err(409, "Le modèle d'embeddings est absent : rien à recalculer.")
        svc.worker.add_job("Réindexation des embeddings", lambda: reembed_all(svc.processor, db))
        return {"ok": True}

    @app.post("/api/admin/retry-errors")
    def retry_errors(_u: str = Depends(user)):
        with db.tx():
            n = db.conn.execute("UPDATE documents SET status = 'pending', error = NULL WHERE status = 'error'").rowcount
        svc.worker.wake()
        return {"ok": True, "documents": n}

    @app.post("/api/admin/reprocess-all")
    def reprocess_all(_u: str = Depends(user)):
        """Analyses every document again (text, OCR orientation, date, title, keywords, summary). Titles and dates typed
        by hand, and tags typed by hand, are kept."""
        with db.tx():
            n = db.conn.execute("UPDATE documents SET status = 'pending', error = NULL WHERE status IN ('done', 'error', 'password_required')").rowcount
        svc.worker.wake()
        return {"ok": True, "documents": n}

    @app.post("/api/admin/purge-missing")
    def purge_missing(_u: str = Depends(user)):
        return {"ok": True, "documents": svc.library.purge_missing()}

    @app.post("/api/admin/forget-passwords")
    def forget_passwords(_u: str = Depends(user)):
        """Deletes every saved password. Documents already unlocked keep their decrypted copy."""
        return {"ok": True, "passwords": svc.vault.forget_all()}

    @app.post("/api/admin/unignore-all")
    def unignore_all(_u: str = Depends(user)):
        n = svc.library.unignore_all()
        svc.scanner.scan_now()
        return {"ok": True, "documents": n}

    @app.post("/api/admin/download-model")
    def download_model(_u: str = Depends(user)):
        if not svc.embedder.available and not svc.embedder.downloading:
            threading.Thread(target=svc.embedder.download, daemon=True).start()
        return {"ok": True, "status": svc.embedder.status()}

    # ------------------------------------------------------------------------------------------ the UI

    static = config.static_dir.resolve()
    index_html = static / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith("api/"):
            raise _err(404, "Introuvable.")
        target = (static / path).resolve() if path else index_html
        if path and target.is_file() and static in target.parents:
            headers = {}
            if "/immutable/" in f"/{path}":
                headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                headers["Cache-Control"] = "no-cache"
            gz = target.with_name(target.name + ".gz")
            if "gzip" in request.headers.get("accept-encoding", "") and gz.is_file():
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
                media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                return FileResponse(gz, media_type=media, headers=headers)
            return FileResponse(target, headers=headers)
        if index_html.is_file():
            return FileResponse(index_html, headers={"Cache-Control": "no-cache"})
        return Response("Papyrus API is running; the UI was not built (ui/ → npm run build).", media_type="text/plain")

    return app
