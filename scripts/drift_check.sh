#!/usr/bin/env bash
# Drift/staleness check for the deployed DB. Runs the two read-only gtfs_pipeline
# checks, prints a timestamped report, and exits nonzero if either reports a
# problem. Intended for a scheduled run (systemd timer) on the DB/app host so
# migration drift or stale aggregates fail loudly before they surface as a 500.
# Read-only: both subcommands only SELECT.
set -uo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
  echo "drift_check: DATABASE_URL is not set; refusing to run (won't guess a DB)." >&2
  exit 2
fi

cd "$(dirname "$0")/.." || exit 3
# Redact credentials (user:pass@) before logging — journald is broadly readable.
db_safe="$(printf '%s' "$DATABASE_URL" | sed -E 's#://[^@/]*@#://***@#; s#\?.*$##')"
echo "=== drift_check $(date -u +%Y-%m-%dT%H:%M:%SZ) DB=${db_safe} ==="

echo "--- check_migrations ---"
poetry run python gtfs_pipeline.py check_migrations
mig=$?

echo "--- check_aggs ---"
poetry run python gtfs_pipeline.py check_aggs
agg=$?

if [ "$mig" -ne 0 ] || [ "$agg" -ne 0 ]; then
  echo "=== drift_check: PROBLEM (migrations=$mig aggs=$agg) ==="
  exit 1
fi
echo "=== drift_check: OK ==="
exit 0
