-include .env
export

DATABASE_URL ?= postgresql://transit:transit@localhost:5433/transit
PORT        ?= 8000

.PHONY: install test fmt lint check serve schema ingest load_static analyze

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

schema:
	docker exec -i transit-pg psql -U transit -d transit < db/schema.sql

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
