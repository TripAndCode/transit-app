#!/usr/bin/env bash
# Fetch archives from Oracle Cloud server then ingest into Postgres.
# Designed to run as a Railway cron job (or any remote host with DATABASE_URL set).
# Does NOT crawl the GTFS website — fetches pre-collected archives only.
#
# Env vars: everything from fetch_archives.sh plus:
#   DATABASE_URL   Postgres connection string
#   AGENCY_ID      Agency ID to ingest (default: 1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENCY_ID="${AGENCY_ID:-1}"

echo "=== fetch_and_ingest started at $(date -u) ==="

# 1. Pull latest archives from Oracle Cloud server
bash "$SCRIPT_DIR/fetch_archives.sh"

# 2. Ingest new RT archives (ON CONFLICT DO NOTHING handles already-ingested files)
echo "==> Ingesting RT archives for agency $AGENCY_ID"
poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" ingest "$SCRIPT_DIR/../raw_archives" --agency-id "$AGENCY_ID"

# 3. Load latest static GTFS (idempotent — re-loads only if new zip appears)
echo "==> Loading static GTFS for agency $AGENCY_ID"
poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" load_static "$SCRIPT_DIR/../raw_archives_static" --agency-id "$AGENCY_ID"

# 4. Re-run aggregations
echo "==> Running analysis for agency $AGENCY_ID"
poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" analyze --agency-id "$AGENCY_ID"

echo "=== fetch_and_ingest complete at $(date -u) ==="
