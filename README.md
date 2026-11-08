# Papyrus

A crawler and search engine for the documents of a NAS, **100 % local**. Papyrus reads the folders you already keep,
finds every PDF and DOCX, reads them (text layer, or OCR for scans), and lets you search them by words and by meaning.
You put your documents where you want (a folder called « bordel » is fine); Papyrus follows them.

French UI, French and Swiss paperwork (payslips, notary papers, invoices…). Code and docs in English.

- **No import step.** Papyrus scans the source folder every minute: a new file shows up, a moved or renamed file is
  followed (same content = same document), a deleted file is hidden, not forgotten.
- **Read-only on your files.** The source folder is mounted `:ro`; nothing in it is ever changed or deleted.
- One Docker container, no Redis, no Postgres, no cloud. SQLite (WAL, FTS5) in its own data folder.
- Nothing leaves the machine, except the one-time download of the embeddings model
  (`intfloat/multilingual-e5-small`, int8 ONNX, ~120 MB), which can be baked into the image or served from a mirror.
- No generative AI: Tesseract (OCR), a small embeddings model, and word statistics.
- Target: Synology DS918+ (Celeron J3455, no AVX); idle RAM ~65 MB (the model is unloaded after 15 idle minutes).

## How it works

```
/volume1/Jerome  ──(read-only)──►  scanner  ──►  worker  ──►  papyrus.db  ──►  UI + search
 your folders                      SHA-256       text, OCR      /volume1/docker/papyrus/data
                                   per file      date, title, tags, summary
```

- **Identity is the SHA-256 of a file's content.** Same content at two paths = one document with two locations.
  Moved or renamed = the location changes, the document keeps its tags, its title and date if you typed them.
  Edited in place = same document, analysed again (what you typed by hand stays).
- **Missing is not deleted.** A document whose files are gone is hidden from lists and search but kept; it comes
  back by itself when a file with the same content reappears, anywhere. An empty or unmounted source folder removes
  nothing (a warning shows in the UI). *Administration → Oublier les documents introuvables* forgets them for good.
- **« Retirer de Papyrus »** (document page) deletes the analysis and keeps that content out of every later scan,
  wherever a copy sits. The file itself stays. *Administration → Réintégrer les documents retirés* undoes it.
- **Skipped**: hidden files and folders, `@eaDir`, `#recycle`, `~$*` Office locks, `*.tmp`, symbolic links, files over
  `SCAN_MAX_FILE_MB`, and files modified in the last `SCAN_SETTLE_SEC` seconds (still being copied).
- **Types of files**: `pdf,docx` by default (`SCAN_EXTENSIONS`; jpg, png, txt and md are already understood and only
  need to be listed there).

### What is read from a document

- **Text**: `pdftotext -layout`; pages with fewer than 50 characters go through Tesseract `fra+eng` at 300 dpi, after
  Tesseract's orientation detection (a scan turned 90° or upside down is read upright). DOCX: `word/document.xml`.
- **Date**: French and ISO formats, keywords first (*fait le*, *période*, a letter's « Grenoble, le … »); contract start
  dates (*à compter du*) and birth dates are pushed away. Without a date in the text, the file's modification date.
- **Title**: the file name, cleaned; when the name says nothing (`scan0012.pdf`, `IMG_2024…`), the heading found in the text.
- **Tags**: year, type (Fiches de paie, Factures, Études… from `rules.yaml`), the main company's name, the words of
  `tag_keywords`, and the document's own distinctive keywords (TF-IDF against the rest of the archive). Tags you add
  by hand are never removed.
- **Summary**: the two or three most representative sentences of the text, quoted as written (not generated).
- **Identifiers**: SIRET/SIREN (Luhn), Swiss IDE, VAT, cadastre references, addresses, contract numbers. IBAN, French
  NIR and Swiss AVS numbers are masked everywhere and never indexed.

### Protected PDFs

- Only *restrictions* (print/copy locked, opens freely): a restriction-free copy is made under `data/unlocked/`, silently.
- A real *password*: the document goes to **À déverrouiller**. You type the password once; it is tried on every other
  waiting PDF, and **kept** (encrypted, tied to the SHA-256 of the document it opened) so every later protected PDF
  found in your folders is tried against it and opens by itself. Untick « Retenir ce mot de passe » to keep nothing.
- Your original is never touched; the decrypted copy under `data/unlocked/` is what Papyrus reads and shows.
- Passwords are stored Fernet-encrypted. The key is `PAPYRUS_KEY` (set it: a copy of the data folder alone is then
  useless) or, by default, a random `data/secret.key` next to the database. They are never logged, never returned by
  the API, never exported. *Administration → Oublier tous les mots de passe* wipes them.

### Search

One bar at the top of the timeline, results as you type. Words are matched whatever the accents; the **last word is
completed** while you type (`salai` → salaire); **one typo is forgiven** (`salare` → salaire); `"exact phrase"`,
`prefix*`, every word required. Folder names are searchable too (`impots 2025`). Results are merged by Reciprocal Rank
Fusion with a **search by meaning** that only keeps documents whose similarity is at least *Similarité minimale*
(0.86 by default: with this model a related document scores 0.86–0.89, an unrelated one 0.77–0.84).
*Administration → Recherche* sets that threshold and switches completion and typo tolerance on or off, live.
Filters: folder (click one in the sidebar), type, year, tag, dates. « Exact seulement » turns the semantic part off.

## Deploy on the Synology

The image is built by the CI at each push to `main` and published as `ghcr.io/krustylebot/papyrus:latest`
(GitHub → the repository's **Packages** → `papyrus` → *Package settings* → *Change visibility* → Public, once; or
`docker login ghcr.io` on the NAS with a token that can read packages).

```bash
mkdir -p /volume1/docker/papyrus/conf /volume1/docker/papyrus/data
chown 1026:100 /volume1/docker/papyrus/conf /volume1/docker/papyrus/data     # the user that can read /volume1/Jerome
cd /volume1/docker/papyrus      # put docker-compose.yml and .env (from .env.example) here
```

Create an account (bcrypt, 12 characters at least; `conf` must be writable for this one command):

```bash
docker run --rm -it --user 1026:100 -v /volume1/docker/papyrus/conf:/conf \
  ghcr.io/krustylebot/papyrus:latest adduser jerome
```

```bash
docker compose up -d
docker compose logs -f papyrus
```

Open `http://<NAS>:8096`. For the Cloudflare tunnel, point the public hostname at `http://papyrus:8080`
(`TRUST_PROXY=true`, `TRUSTED_PROXIES` = the tunnel network, see `.env.example`).

`conf/httpasswd` accepts several lines (several accounts, same rights) and is re-read when it changes. Weak formats
(MD5 `$apr1$`, SHA1, crypt) are refused with a message in the logs. Without a valid account the app refuses to start
and says what to do. `AUTH_DISABLED=true` is for use behind Cloudflare Access only.

The container user (`PUID:PGID`) must be able to **read** every folder to index and **write** `data/`.

### Where things are

Papyrus' state is in `/volume1/docker/papyrus/data` (`PAPYRUS_DATA_HOST`), not with your documents:

```
data/
├── papyrus.db     SQLite (+ -wal/-shm while running): documents, locations, tags, text, embeddings
├── thumbs/        thumbnails
├── unlocked/      decrypted copies of protected PDFs (and restriction-free copies)
├── models/        embeddings model (downloaded at first start)
├── backups/       daily consistent snapshots of the database (7 kept)
└── secret.key     key of the saved PDF passwords (unless PAPYRUS_KEY is set)
```

Your documents are the source of truth, the database is rebuildable: lose `data/` and the next scan rebuilds the index
(only what you typed by hand, and the saved passwords, are lost). Back up `data/` with the rest (Hyper Backup);
the daily snapshot in `backups/` is always consistent, and `python -m papyrus backup` makes one on demand.
Restoring: stop the container, put `data/` back (or a snapshot renamed `papyrus.db`), start it.

### Commands

```bash
docker exec papyrus python -m papyrus scan       # one scan + analysis, no server
docker exec papyrus python -m papyrus backup     # consistent snapshot of the database
docker exec papyrus python -m papyrus export     # JSON of documents, locations, tags (no passwords)
docker exec papyrus python -m papyrus reindex    # recompute every embedding
docker exec papyrus python -m papyrus check      # tools and libraries of the image
```

### The CPU question (no AVX)

`onnxruntime` is tested in a child process before its first use. If its build needs instructions the CPU lacks, the
model is switched off with a banner in the UI and in *Administration* (degraded mode: full-text search and tags)
instead of crashing the app. If that happens on the DS918+: run Papyrus on a mini PC, or build onnxruntime without
AVX and install it in a derived image.

## Rules

`server/papyrus/default_rules.yaml` ships in the image: *types* (keyword weights → a tag and a filter) and
`tag_keywords` (a word → a tag). Put your own at `conf/rules.yaml` (start from `conf/rules.example.yaml`) and press
*Recharger les règles*, then *Réanalyser tous les documents*. Folders do the classifying; types only describe.

Keyboard: `Ctrl/⌘+K` command palette, `/` search, `g t` timeline.

## Development

```bash
# server (Python 3.12)
cd server && pip install -r requirements.txt -r requirements-test.txt
python -m pytest tests -q                       # PAPYRUS_TEST_MODEL=<folder> also runs the semantic tests
AUTH_DISABLED=true PAPYRUS_SOURCE=~/docs PAPYRUS_DATA=/tmp/papyrus PAPYRUS_CONF=/tmp/conf PAPYRUS_STATIC=../ui/dist python -m papyrus

# UI (Svelte + Vite), proxies /api to the server on :8080
cd ui && npm ci && npm run dev
```

The UI is a static SPA built in a Node stage of the Dockerfile and served by FastAPI: no Node at runtime. Fonts and
icons are bundled, nothing is loaded from a CDN.

Layout: `server/papyrus/` — `scanner.py` (the crawler), `pipeline.py` (analysis + worker), `extract.py`, `dates.py`,
`identifiers.py`, `summary.py` (keywords, summary, titles), `search.py`, `vault.py` (saved passwords), `app.py` (API),
`server/tests/`, `ui/src/`, `branding/`.

## Later

An analysis step is one small function (summary, keywords, embedding). A local LLM could take over or complete them —
summaries, deeper questions over the archive — without touching the scanner, the identity of documents or the UI.

## Measurements

Taken on a 4-core x86_64 dev machine (not the J3455, which will be slower):

| | |
|---|---|
| Idle RAM, model unloaded | ~65 MB (limit 150 MB) |
| Model loaded (embedding / semantic search) | ~470 MB peak, back to ~68 MB after the idle unload |
| Embedding of one document | ~0.3 s |
| Search, model warm (small archive) | ~12 ms; first query after an unload ~1.5 s (model load) |
| OCR, one sparse A4 page at 300 dpi | 0.4 s (a dense page takes several seconds) |

Re-measure on the NAS: `docker stats papyrus` at rest, then after a first search.
