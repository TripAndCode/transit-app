#!/usr/bin/env bash
# Fetch archives from Oracle Cloud server then ingest them (raw RT updates go
# to ClickHouse; timetable and aggregate data to Postgres).
# Local-dev replay of recent days only: requires SSH access to the Oracle VM,
# which keeps just the archives not yet uploaded to R2, plus each agency's
# current static zip (spool-cleanup.sh deletes every other one once R2 holds
# it). Production history comes from the live path instead (collector pushes
# into updates_live, promoted daily by the cron), and older days are in R2 for
# backfill (see docs/deploy-railway.md).
# Does NOT crawl the GTFS website — fetches pre-collected archives only.
# Each agency's archives ingest before its newest local timetable loads: a
# load replaces the timetable wholesale, and archived days should join the one
# already loaded. An agency ingest refuses for having no timetable loaded
# (exit 78; REQUIRES_STATIC_TIMETABLE in pipeline/strategies/__init__.py) gets
# its local timetable loaded first and one retry. One still refused is skipped, the rest still run, and the
# script exits 78 once they are done. In the per-agency layout a fetch that
# fails for some agencies still ingests every agency from what is local, then
# exits with the fetch's code, which takes precedence over 78.
#
# Env vars: everything from fetch_archives.sh plus:
#   DATABASE_URL   Postgres connection string
#   AGENCY_ID      Agency ID to ingest (default: 1)
#
# Usage: scripts/fetch_and_ingest.sh

set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,/^set /{/^set /!p;}' "$0" | sed 's/^# \{0,1\}//'; exit 0;; esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENCY_ID="${AGENCY_ID:-1}"

# ingest_agency <archives-dir> <static-dir> <static-glob> <agency-id>
# Returns ingest's exit code. On 78 it loads the agency's timetable from
# <static-dir>, when one matches <static-glob>, and retries once, setting
# loaded_static so the caller does not load the same zip again.
ingest_agency() {
    local archives="$1" static_dir="$2" static_glob="$3" id="$4" code=0
    loaded_static=""
    poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" ingest "$archives" --agency-id "$id" || code=$?
    if [ "$code" -eq 78 ] && compgen -G "$static_dir/$static_glob" > /dev/null; then
        echo "==> [a$id] no timetable loaded; loading static GTFS and retrying"
        poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" load_static "$static_dir" --agency-id "$id" || return "$?"
        loaded_static=1
        code=0
        poetry run python "$SCRIPT_DIR/../gtfs_pipeline.py" ingest "$archives" --agency-id "$id" || code=$?
    fi
    return "$code"
}

echo "=== fetch_and_ingest started at $(date -u) ==="

if [ -n "${COLLECTOR_DATA_DIR:-}" ]; then
    # ── v3: per-agency ingest ────────────────────────────────────────────
    # 1. Pull the latest archives. A fetch that fails for some agencies still
    #    fetched the rest, and ingest is idempotent over what is already local,
    #    so every agency is ingested before the run reports the fetch failure.
    #    An agency the collector does not hold is skipped, not ingested: it has
    #    nothing new, and its ingest would only refuse for having no timetable.
    SKIPPED_AGENCIES_FILE="$(mktemp)"
    export SKIPPED_AGENCIES_FILE
    trap 'rm -f "$SKIPPED_AGENCIES_FILE"' EXIT
    fetch_code=0
    bash "$SCRIPT_DIR/fetch_archives.sh" || fetch_code=$?
    AGENCY_IDS="${AGENCY_IDS:-$(awk -F, 'NR>1 && $3 != "" {print $1}' "$SCRIPT_DIR/../agencies.csv" | tr '\n' ' ')}"
    no_timetable=""
    for id in $AGENCY_IDS; do
        if grep -qx "$id" "$SKIPPED_AGENCIES_FILE"; then
            continue
        fi
        STATIC_DIR="$SCRIPT_DIR/../raw_archives_static/$id"
        echo "==> [a$id] Ingesting RT archives"
        # gtfs_pipeline.py's ingest/analyze exit 75 (EX_TEMPFAIL) on ingest/
        # analyze lock contention -- transient, self-healing on the next
        # scheduled run -- rather than a genuine failure. Caught here (an
        # `||` list is `set -e`-safe) and treated as "skip this agency
        # this run", not a script-ending error: a hard `exit` on contention
        # would abort every agency after this one under `set -euo pipefail`.
        code=0
        ingest_agency "$SCRIPT_DIR/../raw_archives/$id" "$STATIC_DIR" "*.zip" "$id" || code=$?
        if [ "$code" -eq 75 ]; then
            echo "==> [a$id] ingest lock busy, skipping this agency this run"
            continue
        fi
        if [ "$code" -eq 78 ]; then
            echo "==> [a$id] no timetable loaded, skipping this agency" >&2
            no_timetable="$no_timetable $id"
            continue
        fi
        if [ "$code" -ne 0 ]; then
            exit "$code"
        fi

        if [ -z "$loaded_static" ] && compgen -G "$STATIC_DIR/*.zip" > /dev/null; then
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
    if [ -n "$no_timetable" ]; then
        echo "=== fetch_and_ingest skipped agencies with no timetable loaded:$no_timetable (load it, then rerun) ===" >&2
    fi
    if [ "$fetch_code" -ne 0 ]; then
        echo "=== fetch_and_ingest: fetching archives failed (exit $fetch_code); ingested what was already local ===" >&2
        exit "$fetch_code"
    fi
    if [ -n "$no_timetable" ]; then
        exit 78
    fi
else
    # 1. Pull latest archives from Oracle Cloud server
    bash "$SCRIPT_DIR/fetch_archives.sh"

    # ── legacy single-agency path ──────────────────────────────────
    STATIC_DIR="$SCRIPT_DIR/../raw_archives_static"

    # 2. Ingest new RT archives (ClickHouse has no ON CONFLICT: ingest skips files whose
    #    names are already stored for the agency, which keeps reruns idempotent)
    echo "==> Ingesting RT archives for agency $AGENCY_ID"
    code=0
    ingest_agency "$SCRIPT_DIR/../raw_archives" "$STATIC_DIR" "*static*.zip" "$AGENCY_ID" || code=$?
    if [ "$code" -ne 0 ]; then
        exit "$code"
    fi

    # 3. Load latest static GTFS (idempotent — re-loads only if new zip appears).
    #    Skip when no static zip is present locally (Oracle VM may not collect static yet).
    if [ -n "$loaded_static" ]; then
        :
    elif compgen -G "$STATIC_DIR/*static*.zip" > /dev/null; then
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
