# ── Stage 1: build the frontend ──────────────────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python API + bundled static ─────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir poetry==1.8.5

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root --no-interaction

COPY . .
COPY --from=frontend /fe/dist /app/api/static

EXPOSE 8000

# --proxy-headers + --forwarded-allow-ips='*': trust X-Forwarded-For so the
# anon rate-limiter and audit/access logs see the real client IP, not Railway's
# edge. '*' is safe here because Railway's edge is the only network path to the
# container — clients can't connect directly to spoof the header. (Single-quoted
# so the shell doesn't glob the '*'.)
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --proxy-headers --forwarded-allow-ips='*'"]
