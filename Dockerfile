# Papyrus. The build context is the REPOSITORY ROOT:
#     docker build -t papyrus .                          (the embeddings model is downloaded at first start)
#     docker build --build-arg BAKE_MODEL=1 -t papyrus .  (the model, ~120 MB, is baked into the image)

# ---- the UI: Svelte + Vite, built once; .gz siblings so the server never compresses at request time ----
FROM node:22.12.0-bookworm-slim AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build \
 && find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.html' \) -exec gzip -9 -k {} +

# ---- the server: python slim (glibc: musl's allocator holds more memory) ----
FROM python:3.12.8-slim-bookworm
ARG BUILD=dev
ARG BAKE_MODEL=0
LABEL org.opencontainers.image.source="https://github.com/KrustyLeBot/Papyrus" \
      org.opencontainers.image.title="papyrus" \
      org.opencontainers.image.description="Crawler and search engine for the PDF and DOCX files of a NAS: OCR, full-text and semantic search"
# MALLOC_ARENA_MAX: two glibc arenas instead of one per thread keep the idle footprint small.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    PAPYRUS_DATA=/data PAPYRUS_CONF=/conf PAPYRUS_SOURCE=/source PAPYRUS_STATIC=/app/static PAPYRUS_PORT=8080 PAPYRUS_BUILD=$BUILD \
    TZ=Europe/Paris
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng tesseract-ocr-osd poppler-utils libgomp1 tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY server/requirements.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.txt
COPY server/papyrus/ papyrus/
COPY --from=ui /src/ui/dist/ static/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# optional: the embeddings model inside the image (checked by SHA-256), for a NAS that must not download anything
RUN if [ "$BAKE_MODEL" = "1" ]; then \
      python -c "from pathlib import Path; from papyrus.embed import Embedder; d = Path('/opt/papyrus/models'); import sys; sys.exit(0 if Embedder([d], download_dir=d, idle_minutes=0).download() else 1)"; \
    fi
RUN python -m compileall -q /app /usr/local/lib/python3.12 \
 && chmod +x /usr/local/bin/docker-entrypoint.sh \
 && useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin papyrus \
 && mkdir -p /data /conf /source && chown papyrus:papyrus /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve"]
