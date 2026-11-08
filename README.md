# Papyrus

Lightweight, self-hosted, **100 % local** document archive, in the spirit of Paperless-ngx but much smaller.
Upload a document → text extraction / OCR → indexing → tags and **automatic hierarchical grouping** (editable by
hand) → full-text **and** semantic search. French UI, French and Swiss paperwork (payslips, notary papers, invoices…).

- One Docker container, no Redis, no Postgres, no cloud service. SQLite (WAL, FTS5) in the data folder.
- Nothing leaves the machine. The only network access is the one-time download of the embeddings model
  (`intfloat/multilingual-e5-small`, int8 ONNX, ~120 MB), which can be baked into the image or served from a mirror.
- No generative AI: Tesseract (OCR) and a small local embeddings model.
- Target: Synology DS918+ (Celeron J3455, no AVX), idle RAM < 150 MB (the model is unloaded after 15 idle minutes).

## Deploy on the Synology

The image is built by the CI at each push to `main` and published as `ghcr.io/krustylebot/papyrus:latest`
(GitHub → the repository's **Packages** → `papyrus` → *Package settings* → *Change visibility* → Public, once; or
`docker login ghcr.io` on the NAS with a token that can read packages).

```bash
mkdir -p /volume1/docker/papyrus/conf /volume1/Jerome/Documents/Papyrus
cd /volume1/docker/papyrus
# docker-compose.yml and .env.example from this repository
cp .env.example .env && vi .env       # PUID/PGID: `ls -ln /volume1/Jerome/Documents` shows the owner's numbers
```

Create the first account (bcrypt, 12 characters at least; run once per account, `conf` must be writable here):

```bash
docker run --rm -it --user 1026:100 -v /volume1/docker/papyrus/conf:/conf \
  ghcr.io/krustylebot/papyrus:latest adduser jerome
```

Start it (the `tunnel` network is the one the Cloudflare tunnel container is on; create it or edit the compose file):

```bash
docker compose up -d
docker compose logs -f papyrus
```

Open `http://<NAS>:8096`. For the Cloudflare tunnel, point the public hostname at `http://papyrus:8080` and set
`TRUST_PROXY=true` and `TRUSTED_PROXIES` to the tunnel network (see `.env.example`).

`conf/httpasswd` accepts several lines (several accounts, same rights) and is re-read when it changes. Weak
formats (MD5 `$apr1$`, SHA1, crypt) are refused with a message in the logs. Without any valid account the app
refuses to start and says what to do. `AUTH_DISABLED=true` is for use behind Cloudflare Access only.

### Where things are

Everything is in the data folder (`/volume1/Jerome/Documents/Papyrus`), so a plain copy of it (Hyper Backup, rsync…)
is a full backup, and restoring it into a new container restores the system:

```
Papyrus/
├── files/AAAA/MM/<uuid>.<ext>   originals by upload month, opaque names (never modified)
├── thumbs/<uuid>.webp           thumbnails
├── locked/                      password-protected PDFs waiting for their password
├── models/                      embeddings model (downloaded at first start)
├── backups/                     daily consistent snapshots of the database (7 kept)
└── papyrus.db                   SQLite (+ -wal/-shm while running)
```

Copying a live SQLite file can catch it mid-write: the daily snapshot in `backups/` is always consistent, and
`docker exec papyrus python -m papyrus backup` makes one on demand.

### Restore

Stop the container, put the folder back (or only `papyrus.db` from `backups/` renamed to `papyrus.db`, with `files/`),
start the container. Thumbnails missing from a restore are not regenerated automatically: *Relancer l'analyse*
on a document does it.

### Commands

```bash
docker exec papyrus python -m papyrus backup     # consistent snapshot of the database
docker exec papyrus python -m papyrus export     # JSON of documents, groups, tags, assignments
docker exec papyrus python -m papyrus reindex    # recompute every embedding
docker exec papyrus python -m papyrus check      # tools and libraries of the image
```

### The CPU question (no AVX)

`onnxruntime` is tested in a child process before its first use. If its build needs instructions the CPU lacks, the
model is switched off with a banner in the UI and in *Administration* (degraded mode: full-text search, grouping by
rules and identifiers) instead of crashing the app. If that happens on the DS918+: run Papyrus on a mini PC, or
build onnxruntime without AVX and install it in a derived image (`pip install ./onnxruntime-*.whl`).

## What it does

- **Formats**: PDF with a text layer (`pdftotext -layout`), scanned PDF (pages with < 50 characters go through
  Tesseract `fra+eng` at 300 dpi), images (jpg, png, webp, tiff, heic), DOCX, TXT/MD. OCR is sequential
  (`OCR_THREADS`, `OCR_MAX_PAGES`).
- **Protected PDFs**: an owner password alone (copy/print restrictions) is removed silently; a user password parks
  the file in `locked/` until you type it (dialog on upload, *À déverrouiller* in the sidebar, one password tried on
  all waiting PDFs). The password is never stored, logged or returned; the kept copy is the decrypted one.
- **Date, title, type, identifiers** (SIRET/SIREN with Luhn, Swiss IDE, VAT, cadastre references, postal addresses,
  contract numbers; IBAN, NIR and AVS numbers are masked everywhere and never indexed).
- **Grouping**: type → entity → optional sub-entity, a document can sit in several groups. A shared key wins, then
  embedding similarity to a group's centroid, else a new sub-group. Keys shared by sibling groups (a notary's
  address) are ambiguous and ignored. Manual assignments and locked documents are never touched by the automatic
  placement; moving a document by hand teaches the target group.
- **Search**: FTS5 (BM25, accents ignored, `fact*`, `"phrases"`, all words required) merged with semantic cosine
  ranking by Reciprocal Rank Fusion; filters by group, tag, type and date.
- **Rules**: `server/papyrus/default_rules.yaml` ships in the image; put your own at `conf/rules.yaml` (see
  `conf/rules.example.yaml`) and press *Recharger les règles* in *Administration*.

Keyboard: `Ctrl/⌘+K` command palette, `/` search, `u` upload, `g t` / `g s` / `g g` change view.

## Development

```bash
# server (Python 3.12)
cd server && pip install -r requirements.txt -r requirements-test.txt
python -m pytest tests -q                       # PAPYRUS_TEST_MODEL=<folder> also runs the semantic tests
AUTH_DISABLED=true PAPYRUS_DATA=/tmp/papyrus PAPYRUS_CONF=/tmp/conf PAPYRUS_STATIC=../ui/dist python -m papyrus

# UI (Svelte + Vite), proxies /api to the server on :8080
cd ui && npm ci && npm run dev
```

The UI is a static SPA built in a Node stage of the Dockerfile and served by FastAPI: no Node at runtime. Fonts and
icons are bundled, nothing is loaded from a CDN.

Layout: `server/papyrus/` (API in `app.py`, pipeline in `pipeline.py`, grouping in `grouping.py`, extractors in
`identifiers.py` / `dates.py`), `server/tests/`, `ui/src/`, `branding/`, `Dockerfile`, `docker-compose.yml`.

## Measurements

Taken on a 4-core x86_64 dev machine (not the J3455, which will be slower), with the 15-document synthetic corpus of
the test suite:

| | |
|---|---|
| Idle RAM, model unloaded | ~65 MB (limit 150 MB) |
| Model loaded (embedding / semantic search) | ~470 MB peak, back to ~68 MB after the idle unload |
| Embedding of one document | ~0.3 s |
| Search, model warm (10-document archive) | ~12 ms; first query after an unload ~1.5 s (model load) |
| OCR, one sparse A4 page at 300 dpi | 0.4 s (a dense page takes several seconds) |

Re-measure on the NAS: `docker stats papyrus` at rest, then after a first search.
