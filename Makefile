-include .env
export

DATABASE_URL ?= postgresql://transit:transit@localhost:5433/transit
PORT        ?= 8000

.PHONY: all bootstrap doctor bake install test fmt lint check serve db db-down migrate migrate-down fetch fetch-ingest ingest load_static analyze seed-agencies build-rag-index promote-intent-cache prune-query-log verify-secrets

# Default target — first-run setup.
all: bootstrap

# ── First-run bootstrap ──────────────────────────────────────────────────────
# Idempotent: safe to re-run. Brings the project from a clean checkout to
# "ready to `make serve`". Skips data ingest (Path A or B) — that's a separate
# step because it needs either internet/feed_urls or Oracle SSH access.

bootstrap:
	@command -v poetry >/dev/null || { echo "ERROR: poetry not installed. https://python-poetry.org/"; exit 1; }
	@command -v docker >/dev/null || { echo "ERROR: docker not installed. https://www.docker.com/"; exit 1; }
	@command -v npm    >/dev/null || { echo "ERROR: npm not installed. brew install node"; exit 1; }
	@test -f .env || { cp .env.example .env && echo "→ created .env from .env.example — edit it before \`make serve\`"; }
	@echo "→ installing python deps"
	@poetry install
	@echo "→ installing npm deps"
	@cd frontend && npm install --silent
	@echo "→ bringing up Postgres + applying migrations"
	@$(MAKE) db
	@echo "→ seeding agencies from agencies.csv"
	@$(MAKE) seed-agencies
	@echo "→ building SPA + baking into api/static/ for single-origin serve"
	@$(MAKE) frontend-build
	@$(MAKE) bake
	@if command -v pre-commit >/dev/null; then \
		pre-commit install >/dev/null && echo "→ installed pre-commit hooks" \
			|| echo "→ WARNING: pre-commit found but 'pre-commit install' failed — run it manually"; \
	else \
		echo "→ skipping pre-commit hook install (run 'brew install pre-commit' to enable)"; \
	fi
	@echo ""
	@echo "✓ bootstrap done. Next:"
	@echo "    make doctor       # sanity check"
	@echo "    make serve        # then open http://localhost:8000"

# Copy the Vite build into api/static so FastAPI serves SPA + API on one origin.
# Same layout the Dockerfile uses in prod.
bake:
	@rm -rf api/static
	@cp -R frontend/dist api/static
	@echo "→ baked frontend/dist → api/static/"

# ── Sanity check ─────────────────────────────────────────────────────────────
# Reports the state of env, DB container, port 8000, and SSO env without
# starting anything. Exit code 0 always — informational.

doctor:
	@echo "── env ──"
	@test -f .env && echo "  .env present" || echo "  .env MISSING (run \`cp .env.example .env\`)"
	@grep -q '^GROQ_API_KEY=..*' .env 2>/dev/null && echo "  GROQ_API_KEY set" || echo "  GROQ_API_KEY MISSING — Ask tab will 503"
	@n=$$(grep -cE '^(SESSION_SIGNING_KEY|GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|GITHUB_CLIENT_ID|GITHUB_CLIENT_SECRET)=..+' .env 2>/dev/null || true); \
		if [ "$$n" = "5" ]; then echo "  SSO env: all 5 set (login enabled)"; \
		elif [ "$$n" = "0" ]; then echo "  SSO env: none set (anonymous-only)"; \
		else echo "  SSO env: PARTIAL ($$n/5) — startup will fail"; fi
	@echo "── db ──"
	@docker ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null | grep -q '^transit-pg' \
		&& docker ps --format '  {{.Names}}: {{.Status}}' | grep transit-pg \
		|| echo "  transit-pg NOT running — \`make db\`"
	@echo "── port 8000 ──"
	@pid=$$(lsof -ti :8000 2>/dev/null | tr '\n' ' ' || true); \
		if [ -n "$$pid" ]; then echo "  in use by PID(s) $${pid}— kill before \`make serve\`"; \
		else echo "  free"; fi
	@echo "── api/static ──"
	@test -d api/static && echo "  baked SPA present (single-origin works)" \
		|| echo "  not baked — \`make bake\` or run Vite via \`make frontend-dev\`"

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
	DATABASE_URL=$(DATABASE_URL) poetry run uvicorn api.main:app --reload --port $(PORT) --no-access-log

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

# Idempotent: re-runnable, upserts on feed_url uniqueness.
seed-agencies:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py seed_agencies $(if $(CSV),$(CSV),agencies.csv)

# Idempotent: re-runnable, upserts on content_hash uniqueness.
build-rag-index:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py build_rag_index --all-agencies

.PHONY: promote-intent-cache
promote-intent-cache:
	DATABASE_URL=$(DATABASE_URL) poetry run python scripts/promote_intent_cache.py --agency-id $(AGENCY_ID)

prune-query-log:
	DATABASE_URL=$(DATABASE_URL) poetry run python gtfs_pipeline.py prune_query_log --days 90

.PHONY: frontend-install frontend-dev frontend-build

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm install && npm run build

# ── Secret scanning ──────────────────────────────────────────────────────────
# Defense-in-depth. The pre-commit hook scans staged content on every
# commit (see .pre-commit-config.yaml). This target re-scans the full git
# history reachable from HEAD on demand — broader than the commit-time
# hook, so a secret introduced on a feature branch gets caught. Pass
# `--no-git` to gitleaks if you want a working-tree-only scan instead.
# Calls gitleaks directly because pre-commit's gitleaks hook is hard-wired
# to `protect --staged`.

verify-secrets:
	@command -v gitleaks >/dev/null || { echo "ERROR: gitleaks not installed. brew install gitleaks"; exit 1; }
	gitleaks detect --redact --no-banner --source .

# ── Ask eval (CI gate) ────────────────────────────────────────────────────────
# Verifies chip_coverage + builder_coverage = 100% against the gold JSONL.
# Regenerate the gold set after catalog changes: poetry run python scripts/_gen_gold_set.py

ask-eval:
	DATABASE_URL=$(DATABASE_URL) poetry run python scripts/ask_eval.py
