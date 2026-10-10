# ── Stage 1: build the frontend ──────────────────────────────────────────────
FROM node:26-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* frontend/.npmrc* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Strip the build-time-only Vite manifest dir before it ships into the
# production static tree, where api/main.py's SPA fallback would otherwise
# serve it publicly. Shared with Makefile's `bake` target via
# scripts/strip_vite_manifest.sh — see that file for why — so the two build
# paths (container image vs. `make bake`) can't drift out of sync.
COPY scripts/strip_vite_manifest.sh /tmp/strip_vite_manifest.sh
RUN sh /tmp/strip_vite_manifest.sh dist

# ── Stage 2: Python API + bundled static ─────────────────────────────────────
FROM python:3.14-slim
WORKDIR /app

RUN pip install --no-cache-dir poetry==2.5.1

COPY pyproject.toml poetry.lock ./
# The `embeddings` group (sentence-transformers, and transitively torch,
# transformers, scikit-learn, scipy) is optional and deliberately excluded
# here by `--only main`: `pipeline.query.embeddings.Embedder`'s import is
# already wrapped in try/except and every caller falls through to the
# LLM-only path when it's unavailable (see that module's docstring — API
# startup never aborts on embedding failure). That whole dependency subtree
# is several GB and unused until a RAG index actually exists; carrying it in
# the deploy image bought nothing but slower builds and a slower cold start.
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root --no-interaction

# Run as an unprivileged user: a container escape or dependency RCE then
# lands with no write access outside /app and no root inside it. The user is
# created before the COPYs so they can set ownership directly: a later
# `chown -R` would instead rewrite every copied path into a new layer, and
# because layer diffs are file-granular that duplicates the whole tree's bytes
# in the image for a metadata-only change. /app itself is chowned too, so the
# ingest strategies can still create their per-agency directories under it.
RUN adduser --system --group --no-create-home app \
    && chown app:app /app

COPY --chown=app:app . .
COPY --from=frontend --chown=app:app /fe/dist /app/api/static

USER app

EXPOSE 8000

# Poll the same liveness endpoint a load balancer would, on the same port
# uvicorn actually binds (respects $PORT, matching the CMD below) — so a
# hung/deadlocked process gets marked unhealthy instead of serving errors
# indefinitely.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "\
import os, sys, urllib.request; \
port = os.environ.get('PORT', '8000'); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status == 200 else 1)"

# Railway's edge is the only network path to the container and the one proxy
# in front of it, so the client address is the X-Forwarded-For entry that edge
# appended: the rightmost (api/middleware/forwarded.py). A deployment behind
# more proxies sets FORWARDED_HOPS to their count. uvicorn's own proxy-header
# handling stays off, since trusting every peer makes it read the leftmost
# entry, which a client can write.
ENV FORWARDED_HOPS=1
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --no-proxy-headers"]
