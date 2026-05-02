-include .env
export

DATABASE_URL ?= postgresql://transit:transit@localhost:5433/transit
PORT        ?= 8000

.PHONY: install test fmt lint check serve db db-down migrate migrate-down fetch fetch-ingest ingest load_static analyze

install:
	poetry install

# ── Quality ──────────────────────────────────────────────────────────────────

fmt:
	poetry run ruff format .

lint:
	poetry run ruff check .

check: fmt lint test

# ── Tests ─────────────────────────────────────────────────────────────────────

test:
	DATABASE_URL=$(DATABASE_URL) poetry run pytest

# ── Server ───────────────────────────────────────────────────────────────────

serve:
	DATABASE_URL=$(DATABASE_URL) poetry run uvicorn api.main:app --reload --port $(PORT)

# ── Database ─────────────────────────────────────────────────────────────────

db:
	docker compose up -d --build
	docker compose exec db sh -c 'until pg_isready -U transit -d transit; do sleep 1; done'
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py migrate up

db-down:
	docker compose down

migrate:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py migrate up

migrate-down:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py migrate down $(if $(TARGET),--target $(TARGET),)

# ── Data fetch (pull from Oracle Cloud collection server) ────────────────────
# Requires: ORACLE_HOST, ORACLE_USER, ORACLE_SSH_KEY or ORACLE_SSH_KEY_PATH

fetch:
	bash scripts/fetch_archives.sh

fetch-ingest:
	bash scripts/fetch_and_ingest.sh

# ── Pipeline ─────────────────────────────────────────────────────────────────
# Usage: make ingest FOLDER=./raw_archives
# Usage: make load_static PATH=./raw_archives_static
# Usage: make ingest FOLDER=./raw_archives AGENCY_ID=1

ingest:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py ingest $(FOLDER) $(if $(AGENCY_ID),--agency-id $(AGENCY_ID),)

load_static:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py load_static $(PATH) $(if $(AGENCY_ID),--agency-id $(AGENCY_ID),)

analyze:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py analyze $(if $(AGENCY_ID),--agency-id $(AGENCY_ID),)
