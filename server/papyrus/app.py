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

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__, identifiers
from .auth import CSRF_COOKIE, SESSION_COOKIE, Auth, client_ip, cookie_secure
from .config import Config
from .db import blob_to_vec
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


class DocPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    doc_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    locked: bool | None = None


class TagBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = None
    move: bool = False  # parent_id is applied only when move is true (parent_id null = top level)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class MergeBody(BaseModel):
    into: int


class AssignBody(BaseModel):
    doc_ids: list[int] = Field(min_length=1, max_length=1000)
    from_group: int | None = None


class ReclassifyBody(BaseModel):
    group_id: int | None = None


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


class GroupIndex:
    """Every group, for naming a document's groups by their path (Fiches de paie / Atelier Morel SAS)."""

    def __init__(self, conn):
        self.rows = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, parent_id, name, color, is_inbox, type_name, auto FROM groups")}

    def root(self, gid: int) -> dict:
        row = self.rows[gid]
        seen = set()
        while row["parent_id"] is not None and row["parent_id"] in self.rows and row["id"] not in seen:
            seen.add(row["id"])
            row = self.rows[row["parent_id"]]
        return row

    def path(self, gid: int) -> str:
        names, row, seen = [], self.rows.get(gid), set()
        while row is not None and row["id"] not in seen:
            seen.add(row["id"])
            names.append(row["name"])
            row = self.rows.get(row["parent_id"]) if row["parent_id"] is not None else None
        return " / ".join(reversed(names))

    def describe(self, gid: int, source: str | None = None) -> dict | None:
        if gid not in self.rows:
            return None
        row = self.rows[gid]
        out = {"id": gid, "name": row["name"], "path": self.path(gid), "color": self.root(gid)["color"] or "#7C8AA5",
               "inbox": bool(row["is_inbox"])}
        if source:
            out["source"] = source
        return out


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
        gindex = GroupIndex(db.conn)
        groups: dict[int, list] = {i: [] for i in ids}
        for r in db.conn.execute(f"SELECT doc_id, group_id, source FROM doc_groups WHERE doc_id IN ({marks})", ids):
            described = gindex.describe(r["group_id"], r["source"])
            if described:
                groups[r["doc_id"]].append(described)
        tags: dict[int, list] = {i: [] for i in ids}
        for r in db.conn.execute(f"SELECT dt.doc_id, t.id, t.name, dt.source FROM doc_tags dt JOIN tags t ON "
                                 f"t.id = dt.tag_id WHERE dt.doc_id IN ({marks}) ORDER BY t.name", ids):
            tags[r["doc_id"]].append({"id": r["id"], "name": r["name"], "source": r["source"]})
        out = []
        for r in rows:
            item = {
                "id": r["id"], "title": r["title"], "date": r["doc_date"], "date_source": r["date_source"],
                "created_at": r["created_at"], "status": r["status"], "ext": r["ext"], "pages": r["pages"],
                "size": r["size"], "original_name": r["original_name"], "error": r["error"],
                "groups": sorted(groups[r["id"]], key=lambda g: g["path"]), "tags": tags[r["id"]],
                "types": json.loads(r["types"] or "[]"),
                "thumb": f"/api/documents/{r['id']}/thumb?v={quote(r['updated_at'])}" if r["status"] == "done" else None,
                "locked": bool(r["locked"]), "suggestion_applied": bool(r["suggestion_applied"]),
                "summary": r["summary"] if full else (r["summary"] or "")[:200],
            }
            if full:
                item.update({
                    "text": r["text"], "ocr": bool(r["ocr"]), "unlocked_at": r["unlocked_at"],
                    "processed_at": r["processed_at"], "title_manual": bool(r["title_manual"]),
                    "has_embedding": r["embedding"] is not None,
                    "suggestions": [dict(s, group=gindex.describe(s["group_id"]))
                                    for s in json.loads(r["suggestions"] or "[]") if gindex.describe(s["group_id"])],
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

    def group_ids_for(group: int | None, include_children: bool) -> list[int] | None:
        if group is None:
            return None
        return svc.grouper.descendants(group) if include_children else [group]

    # ------------------------------------------------------------------------------------------ documents

    @app.get("/api/documents")
    def list_documents(_u: str = Depends(user), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                       group: int | None = None, children: bool = True, tag: str | None = None,
                       type: str | None = None, status: str | None = None,
                       date_from: str | None = None, date_to: str | None = None):
        clauses, params = [], []
        if status == "all":
            pass
        elif status:
            clauses.append("status = ?")
            params.append(status)
        else:
            clauses.append("status != 'password_required'")
        gids = group_ids_for(group, children)
        if gids is not None:
            clauses.append(f"id IN (SELECT doc_id FROM doc_groups WHERE group_id IN ({','.join('?' * len(gids))}))")
            params += gids
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
            f"error, types, locked, suggestion_applied, summary FROM documents {where} "
            f"ORDER BY COALESCE(doc_date, substr(created_at, 1, 10)) DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        return {"items": serialize(rows), "total": total, "offset": offset, "limit": limit}

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: int, _u: str = Depends(user)):
        row = get_doc(doc_id)
        item = serialize([row], full=True)[0]
        keys = [dict(k) for k in db.conn.execute("SELECT key, value, label FROM doc_keys WHERE doc_id = ? ORDER BY rowid",
                                                 (doc_id,))]
        item["keys"] = keys
        item["sensitive"] = identifiers.mask_sensitive(row["text"])[1] + [
            {"kind": "masked", "label": "Masqué", "masked": m} for m in []]
        # the stored text is already masked; list what was masked from the bullets left in it
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
            if body.locked is not None:
                db.conn.execute("UPDATE documents SET locked = ? WHERE id = ?", (int(body.locked), doc_id))
            touch(doc_id)
            index_document(db, doc_id)
            if body.doc_date is not None and row["status"] == "done":
                svc.processor.auto_tags(doc_id)
        return document(doc_id, _u)

    @app.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: int, _u: str = Depends(user)):
        get_doc(doc_id)
        svc.library.delete(doc_id)
        return {"ok": True}

    @app.post("/api/documents/{doc_id}/reprocess")
    def reprocess(doc_id: int, _u: str = Depends(user)):
        row = get_doc(doc_id)
        if row["status"] == "password_required":
            raise _err(409, "Ce PDF doit d'abord être déverrouillé.")
        with db.tx():
            db.conn.execute("UPDATE documents SET status = 'pending', error = NULL WHERE id = ?", (doc_id,))
        svc.worker.wake()
        return {"ok": True}

    @app.get("/api/documents/{doc_id}/file")
    def document_file(doc_id: int, download: bool = False, _u: str = Depends(user)):
        row = get_doc(doc_id)
        if row["status"] == "password_required":
            raise _err(409, "Ce PDF est encore protégé.")
        path = config.data_dir / row["path"]
        if not path.is_file():
            raise _err(404, "Fichier introuvable sur le disque.")
        disposition = "attachment" if download else "inline"
        name = row["original_name"]
        ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name)
        headers = {"Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
                   "Cache-Control": "private, max-age=3600"}
        if row["ext"] != "pdf":
            # an uploaded image or text is never a page of this site: no script runs from it
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

    @app.post("/api/documents/{doc_id}/suggestions/{group_id}/accept")
    def accept_suggestion(doc_id: int, group_id: int, _u: str = Depends(user)):
        get_doc(doc_id)
        svc.grouper.add_manual(doc_id, group_id)
        with db.tx():
            touch(doc_id)
        return document(doc_id, _u)

    # ------------------------------------------------------------------------------------------ upload & unlock

    @app.post("/api/upload")
    def upload(request: Request, files: list[UploadFile] = File(...), _u: str = Depends(user)):
        limit = config.max_upload_mb * 1024 * 1024
        results = []
        for item in files:
            name = item.filename or "document"
            tmp = svc.library.tmp_dir() / f"up-{uuid.uuid4().hex}"
            size = 0
            too_big = False
            with tmp.open("wb") as out:
                while True:
                    block = item.file.read(1 << 20)
                    if not block:
                        break
                    size += len(block)
                    if size > limit:
                        too_big = True
                        break
                    out.write(block)
            if too_big:
                tmp.unlink(missing_ok=True)
                results.append({"name": name, "status": "rejected",
                                "message": f"Fichier trop volumineux (limite {config.max_upload_mb} Mo)."})
                continue
            intake = svc.library.ingest(tmp, name)
            results.append({"name": intake.name or name, "status": intake.status, "id": intake.doc_id,
                            "message": intake.message, "title": intake.title})
        svc.worker.wake()
        return {"results": results}

    @app.get("/api/locked")
    def locked(_u: str = Depends(user)):
        return {"items": svc.library.locked()}

    @app.post("/api/documents/{doc_id}/unlock")
    def unlock(doc_id: int, body: UnlockBody, _u: str = Depends(user)):
        first = svc.library.unlock(doc_id, body.password)
        if first.status == "rejected":
            raise _err(409, first.message)
        if first.status == "password_required":
            raise _err(400, "Mot de passe incorrect. Le document reste en attente.")
        others = []
        if body.try_others:
            for item in svc.library.locked():
                result = svc.library.unlock(item["id"], body.password)
                if result.status in ("queued", "duplicate"):
                    others.append({"id": result.doc_id, "name": result.name, "status": result.status})
        svc.worker.wake()
        return {"status": first.status, "id": first.doc_id, "others": others,
                "remaining": len(svc.library.locked())}

    # ------------------------------------------------------------------------------------------ search

    @app.get("/api/search")
    def search(q: str = Query("", max_length=500), exact: bool = False, group: int | None = None, children: bool = True,
               tag: str | None = None, type: str | None = None, date_from: str | None = None,
               date_to: str | None = None, limit: int = Query(50, ge=1, le=200), _u: str = Depends(user)):
        if not q.strip():
            return list_documents(_u, 0, limit, group, children, tag, type, None, date_from, date_to)
        found = svc.searcher.search(q, exact, group_ids_for(group, children), tag, type, date_from, date_to, limit)
        ids = found["ids"]
        rows = {r["id"]: r for r in db.conn.execute(
            f"SELECT * FROM documents WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
        items = serialize([rows[d] for d in ids if d in rows])
        for item in items:
            item["snippet"] = found["snippets"].get(item["id"]) or ""
            item["match"] = found["how"].get(item["id"], "")
            if not item["snippet"]:
                text = rows[item["id"]]["text"]
                item["snippet_plain"] = " ".join(text[:240].split())
        return {"items": items, "total": found["total"], "elapsed_ms": found["elapsed_ms"],
                "semantic": found["semantic"]}

    # ------------------------------------------------------------------------------------------ groups

    @app.get("/api/groups")
    def groups(_u: str = Depends(user)):
        rows = db.conn.execute("SELECT id, parent_id, name, color, type_name, is_inbox, auto, centroid_n FROM groups").fetchall()
        members: dict[int, set] = {r["id"]: set() for r in rows}
        for r in db.conn.execute("SELECT dg.doc_id, dg.group_id FROM doc_groups dg JOIN documents d ON d.id = dg.doc_id "
                                 "WHERE d.status != 'password_required'"):
            members.setdefault(r["group_id"], set()).add(r["doc_id"])
        children: dict[int | None, list[int]] = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])

        def total(gid: int, seen=None) -> set:
            seen = seen or set()
            if gid in seen:
                return set()
            seen.add(gid)
            out = set(members.get(gid, ()))
            for child in children.get(gid, []):
                out |= total(child, seen)
            return out

        key_counts = {r["group_id"]: r["n"] for r in db.conn.execute(
            "SELECT group_id, COUNT(*) AS n FROM group_keys GROUP BY group_id")}
        out = []
        for r in rows:
            out.append({"id": r["id"], "parent_id": r["parent_id"], "name": r["name"], "color": r["color"],
                        "type": r["type_name"], "inbox": bool(r["is_inbox"]), "auto": bool(r["auto"]),
                        "count": len(members.get(r["id"], ())), "total": len(total(r["id"])),
                        "keys": key_counts.get(r["id"], 0)})
        out.sort(key=lambda g: (g["inbox"], g["name"].lower()))
        return {"items": out}

    def get_group(group_id: int):
        row = db.conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if row is None:
            raise _err(404, "Groupe introuvable.")
        return row

    @app.get("/api/groups/{group_id}")
    def group(group_id: int, _u: str = Depends(user)):
        row = get_group(group_id)
        keys = db.conn.execute("SELECT key, value, label, source FROM group_keys WHERE group_id = ? ORDER BY key, label",
                               (group_id,)).fetchall()
        siblings_keys: dict[tuple, int] = {}
        if row["parent_id"] is not None:
            for r in db.conn.execute("SELECT key, value, COUNT(DISTINCT group_id) AS n FROM group_keys WHERE group_id IN "
                                     "(SELECT id FROM groups WHERE parent_id = ?) GROUP BY key, value", (row["parent_id"],)):
                siblings_keys[(r["key"], r["value"])] = r["n"]
        blacklist = svc.rules.rules.blacklist
        gindex = GroupIndex(db.conn)
        return {"id": row["id"], "name": row["name"], "parent_id": row["parent_id"], "color": row["color"],
                "path": gindex.path(group_id), "root_color": gindex.root(group_id)["color"], "type": row["type_name"],
                "inbox": bool(row["is_inbox"]),
                "keys": [{"key": k["key"], "value": k["value"], "label": k["label"], "source": k["source"],
                          "ambiguous": siblings_keys.get((k["key"], k["value"]), 0) > 1 or
                          (k["key"], k["value"]) in blacklist} for k in keys]}

    @app.post("/api/groups")
    def create_group(body: GroupBody, _u: str = Depends(user)):
        if body.parent_id is not None:
            get_group(body.parent_id)
        gid = svc.grouper.create(body.name.strip(), body.parent_id, body.color)
        return group(gid, _u)

    @app.patch("/api/groups/{group_id}")
    def patch_group(group_id: int, body: GroupPatch, _u: str = Depends(user)):
        row = get_group(group_id)
        with db.tx():
            if body.name is not None:
                db.conn.execute("UPDATE groups SET name = ?, auto = 0 WHERE id = ?", (body.name.strip(), group_id))
            if body.color is not None:
                db.conn.execute("UPDATE groups SET color = ? WHERE id = ?", (body.color, group_id))
        if body.move:
            if row["is_inbox"]:
                raise _err(400, "Le groupe « À classer » reste à la racine.")
            if body.parent_id is not None:
                get_group(body.parent_id)
            try:
                svc.grouper.reparent(group_id, body.parent_id)
            except ValueError as error:
                raise _err(400, str(error)) from None
        return group(group_id, _u)

    @app.post("/api/groups/{group_id}/merge")
    def merge_group(group_id: int, body: MergeBody, _u: str = Depends(user)):
        source, target = get_group(group_id), get_group(body.into)
        if source["is_inbox"] or target["is_inbox"]:
            raise _err(400, "Le groupe « À classer » ne peut pas être fusionné.")
        try:
            svc.grouper.merge(group_id, body.into)
        except ValueError as error:
            raise _err(400, str(error)) from None
        return group(body.into, _u)

    @app.delete("/api/groups/{group_id}")
    def delete_group(group_id: int, _u: str = Depends(user)):
        get_group(group_id)
        try:
            svc.grouper.delete(group_id)
        except ValueError as error:
            raise _err(400, str(error)) from None
        return {"ok": True}

    @app.delete("/api/groups/{group_id}/keys")
    def delete_group_key(group_id: int, key: str, value: str, _u: str = Depends(user)):
        get_group(group_id)
        with db.tx():
            db.conn.execute("DELETE FROM group_keys WHERE group_id = ? AND key = ? AND value = ?", (group_id, key, value))
        return group(group_id, _u)

    @app.post("/api/groups/{group_id}/documents")
    def assign(group_id: int, body: AssignBody, _u: str = Depends(user)):
        get_group(group_id)
        for doc_id in body.doc_ids:
            get_doc(doc_id)
            svc.grouper.move(doc_id, body.from_group, group_id)
            with db.tx():
                touch(doc_id)
        return {"ok": True}

    @app.delete("/api/groups/{group_id}/documents/{doc_id}")
    def unassign(group_id: int, doc_id: int, _u: str = Depends(user)):
        get_group(group_id)
        get_doc(doc_id)
        svc.grouper.remove(doc_id, group_id)
        with db.tx():
            touch(doc_id)
        return {"ok": True}

    @app.post("/api/groups/reclassify")
    def reclassify(body: ReclassifyBody, _u: str = Depends(user)):
        if body.group_id is not None:
            get_group(body.group_id)
        count = svc.grouper.reclassify(body.group_id, svc.processor.retype)
        return {"ok": True, "documents": count}

    # ------------------------------------------------------------------------------------------ tags & types

    @app.get("/api/tags")
    def tags(_u: str = Depends(user)):
        rows = db.conn.execute("SELECT t.id, t.name, COUNT(dt.doc_id) AS n FROM tags t LEFT JOIN doc_tags dt ON "
                               "dt.tag_id = t.id GROUP BY t.id ORDER BY n DESC, t.name").fetchall()
        return {"items": [{"id": r["id"], "name": r["name"], "count": r["n"]} for r in rows]}

    @app.get("/api/types")
    def types(_u: str = Depends(user)):
        return {"items": [{"name": t.name, "color": t.color, "subgroup_by": t.subgroup_by}
                          for t in svc.rules.rules.types]}

    # ------------------------------------------------------------------------------------------ queue & admin

    @app.get("/api/queue")
    def queue(_u: str = Depends(user)):
        rows = db.conn.execute(
            "SELECT id, title, original_name, status, error, created_at FROM documents WHERE status IN "
            "('pending', 'processing', 'error') ORDER BY CASE status WHEN 'processing' THEN 0 WHEN 'pending' THEN 1 "
            "ELSE 2 END, id LIMIT 200").fetchall()
        locked_count = db.conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'password_required'").fetchone()[0]
        return {"items": [dict(r) for r in rows], "current": svc.worker.current, "jobs": svc.worker.jobs(),
                "locked": locked_count}

    @app.get("/api/admin/status")
    def admin_status(_u: str = Depends(user)):
        counts = {r["status"]: r["n"] for r in db.conn.execute("SELECT status, COUNT(*) AS n FROM documents GROUP BY status")}
        size = db.conn.execute("SELECT COALESCE(SUM(size), 0) FROM documents").fetchone()[0]
        db_size = sum(p.stat().st_size for p in config.data_dir.glob("papyrus.db*") if p.is_file())
        missing = db.conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'done' AND embedding IS NULL").fetchone()[0]
        return {
            "documents": sum(counts.values()), "by_status": counts, "files_bytes": size, "db_bytes": db_size,
            "groups": db.conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
            "tags": db.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            "worker": {"alive": bool(svc.worker.thread and svc.worker.thread.is_alive()), "current": svc.worker.current,
                       "jobs": svc.worker.jobs()},
            "model": {"status": svc.embedder.status(), "error": svc.embedder.error, "missing_embeddings": missing},
            "rules": {"source": svc.rules.rules.source, "types": len(svc.rules.rules.types)},
            "memory_mb": _rss_mb(), "version": __version__, "build": config.build,
            "auth": {"disabled": auth.disabled, "accounts": len(svc.accounts.users) if svc.accounts else 0,
                     "problems": svc.accounts.problems if svc.accounts else []},
        }

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
        """Analyses every document again (text, OCR orientation, date, title, keywords, summary, groups). Manual
        groups, tags typed by hand, typed titles and dates, and locked documents keep what they have."""
        with db.tx():
            n = db.conn.execute("UPDATE documents SET status = 'pending', error = NULL WHERE status IN ('done', 'error')").rowcount
        svc.worker.wake()
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
