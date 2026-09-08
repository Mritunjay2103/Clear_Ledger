# ClearLedger — one image that serves the API and the built frontend.
#
# Stage 1 builds the React bundle. Stage 2 installs Python dependencies against
# a wheel cache. Stage 3 is the runtime: no compilers, no node, no build caches,
# and a non-root user that owns only the data directory.

# --- 1. frontend build -------------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- 2. python dependencies --------------------------------------------------
FROM python:3.11-slim-bookworm AS python-deps
WORKDIR /build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


# --- 3. runtime --------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# Tesseract is the OCR engine; without it, image-only pages route to review
# instead of being read. It is a real dependency, so it ships in the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend \
    DATA_DIR=/data \
    STATIC_DIR=/app/frontend/dist \
    APP_ENV=production \
    PORT=8000

COPY --from=python-deps /opt/venv /opt/venv

WORKDIR /app
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY prompts/ ./prompts/
COPY data/reference/ ./data/reference/
COPY data/samples/ ./data/samples/
COPY --from=frontend /build/dist ./frontend/dist

# The data directory is the only writable path, and it is the volume mount
# point: an image rebuild must never be able to lose a processed run.
RUN useradd --system --uid 10001 --create-home clearledger \
    && mkdir -p /data \
    && chown -R clearledger:clearledger /data /app
USER clearledger
VOLUME ["/data"]

EXPOSE 8000

# Readiness reports on the database and the data directory, so an unhealthy
# container is one that genuinely cannot accept work.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/readiness" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --app-dir /app/backend"]
