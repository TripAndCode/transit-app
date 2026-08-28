# ── Stage 1: build the frontend ──────────────────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# dist/.vite/manifest.json (from vite.config.ts's build.manifest: true,
# used by scripts/check-entry-chunk.mjs) is a build-time-only artifact —
# strip it so it doesn't ship into the production static tree, where
# api/main.py's SPA fallback would serve it publicly.
RUN rm -rf dist/.vite

# ── Stage 2: Python API + bundled static ─────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir poetry==1.8.5

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root --no-interaction

# Bake the Ask-tab embedder into the image. Without this the container downloads
# the model (~hundreds of MB) from HuggingFace on every cold start — a ~50s boot
# delay before /health passes AND a runtime dependency on HF being reachable.
# HF_HOME pins the cache path so the build-time download and the runtime load
# resolve to the same place. Placed before `COPY . .` so editing app code
# doesn't invalidate this heavy layer. Keep the id in sync with
# pipeline/query/embeddings.py:_DEFAULT_MODEL.
ENV HF_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY . .
COPY --from=frontend /fe/dist /app/api/static

EXPOSE 8000

# --proxy-headers + --forwarded-allow-ips='*': trust X-Forwarded-For so the
# anon rate-limiter and audit/access logs see the real client IP, not Railway's
# edge. '*' is safe here because Railway's edge is the only network path to the
# container — clients can't connect directly to spoof the header. (Single-quoted
# so the shell doesn't glob the '*'.)
#
# CAVEAT (unverified as of 2026-07): this only holds if Railway's edge fully
# *replaces* any client-supplied X-Forwarded-For rather than appending to it —
# uvicorn's ProxyHeadersMiddleware with '*' trusts the leftmost entry
# unconditionally, so an appended (not replaced) header would let an external
# client set an arbitrary/rotating leftmost IP and defeat both the per-IP rate
# limit and the sessions.ip/login_events.ip audit trail. Railway's own support
# forum has contradictory answers on which it does (an employee says it strips
# client-supplied XFF; a separate empirical report suggests otherwise) — verify
# against the actual deployment (or Railway support) before treating this as
# settled, especially before relying on it for anything security-load-bearing.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --proxy-headers --forwarded-allow-ips='*'"]
