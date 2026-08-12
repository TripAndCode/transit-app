#!/usr/bin/env bash
# Fetch archives from Oracle Cloud server then ingest into Postgres.
# Local-dev replay path: requires SSH access to the Oracle VM. Production
# ingests the same Oracle archives, but Oracle uploads them to object storage
# (R2/S3) and a daily Railway scheduled job pulls + ingests them over HTTPS —
# no SSH, no public DB (see docs/deploy-railway.md). `ingest_live` is the
# no-Oracle fallback.
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

if [ -n "${COLLECTOR_DATA_DIR:-}" ]; then
    # ── v3: per-agency ingest ────────────────────────────────────────────
    AGENCY_IDS="${AGENCY_IDS:-$(awk -F, 'NR>1 && $3 != "" {print $1}' "$SCRIPT_DIR/../agencies.csv" | tr '\n' ' ')}"
    for id in $AGENCY_IDS; do
        echo "==> [a$id] Ingesting RT archives"
        # gtfs_pipeline.py's ingest/analyze exit 75 (EX_TEMPFAIL) on ingest/
        # analyze lock contention -- transient, self-healing on the next
        # scheduled run -- rather than a genuine failure. Caught here (an
        # `if` condition is `set -e`-safe) and treated as "skip this agency
        # this run", not a script-ending error: a hard `exit` on contention
        # would abort every agency after this one under `set -euo pipefail`.
        if poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" ingest \
            "$SCRIPT_DIR/../raw_archives/$id" --agency-id "$id"; then
            :
        else
            code=$?
            if [ "$code" -eq 75 ]; then
                echo "==> [a$id] ingest lock busy, skipping this agency this run"
                continue
            fi
            exit "$code"
        fi

        STATIC_DIR="$SCRIPT_DIR/../raw_archives_static/$id"
        if compgen -G "$STATIC_DIR/*.zip" > /dev/null; then
            echo "==> [a$id] Loading static GTFS"
            poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" load_static \
                "$STATIC_DIR" --agency-id "$id"
        fi

        echo "==> [a$id] Running analysis"
        if poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" analyze --agency-id "$id"; then
            :
        else
            code=$?
            if [ "$code" -eq 75 ]; then
                echo "==> [a$id] analyze lock busy, skipping this agency this run"
                continue
            fi
            exit "$code"
        fi
    done
else
    # ── legacy single-agency path (unchanged) ────────────────────────────
    # 2. Ingest new RT archives (ON CONFLICT DO NOTHING handles already-ingested files)
    echo "==> Ingesting RT archives for agency $AGENCY_ID"
    poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" ingest "$SCRIPT_DIR/../raw_archives" --agency-id "$AGENCY_ID"

    # 3. Load latest static GTFS (idempotent — re-loads only if new zip appears).
    #    Skip when no static zip is present locally (Oracle VM may not collect static yet).
    STATIC_DIR="$SCRIPT_DIR/../raw_archives_static"
    if compgen -G "$STATIC_DIR/*static*.zip" > /dev/null; then
        echo "==> Loading static GTFS for agency $AGENCY_ID"
        poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" load_static "$STATIC_DIR" --agency-id "$AGENCY_ID"
    else
        echo "==> No *_static.zip in $STATIC_DIR — skipping load_static"
    fi

    # 4. Re-run aggregations
    echo "==> Running analysis for agency $AGENCY_ID"
    poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" analyze --agency-id "$AGENCY_ID"
fi

echo "=== fetch_and_ingest complete at $(date -u) ==="
