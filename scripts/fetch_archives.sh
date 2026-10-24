#!/usr/bin/env bash
# Pull GTFS-RT archives (tar.gz) and static ZIPs from the Oracle Cloud collection server.
# Does NOT crawl the GTFS website — that runs separately on the remote server.
#
# Required env vars:
#   ORACLE_HOST      IP or hostname of the collection server
#   ORACLE_SSH_KEY   Base64-encoded private key  — OR —
#   ORACLE_SSH_KEY_PATH  Path to private key file (used if ORACLE_SSH_KEY is not set)
#
# Optional:
#   ORACLE_USER      SSH user                      (default: opc)
#   ORACLE_RT_DIR    Remote RT archive directory   (default: /home/opc/app/transportation_analysis/archive)
#   ORACLE_STATIC_DIR Remote static archive dir    (default: /home/opc/app/transportation_analysis/static_archive)
#   LOCAL_RT_DIR     Local destination for RT      (default: ./raw_archives)
#   LOCAL_STATIC_DIR Local destination for static  (default: ./raw_archives_static)
#   SINCE            YYYYMMDD: fetch only RT archives of that day and later
#                    (per-agency layout only; timetables are always fetched)
#
# Usage: scripts/fetch_archives.sh

set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,/^set /{/^set /!p;}' "$0" | sed 's/^# \{0,1\}//'; exit 0;; esac

ORACLE_HOST="${ORACLE_HOST:?ORACLE_HOST is required}"
ORACLE_USER="${ORACLE_USER:-opc}"
ORACLE_RT_DIR="${ORACLE_RT_DIR:-/home/opc/app/transportation_analysis/archive}"
ORACLE_STATIC_DIR="${ORACLE_STATIC_DIR:-/home/opc/app/transportation_analysis/static_archive}"
LOCAL_RT_DIR="${LOCAL_RT_DIR:-$(dirname "$0")/../raw_archives}"
LOCAL_STATIC_DIR="${LOCAL_STATIC_DIR:-$(dirname "$0")/../raw_archives_static}"

if [ -n "${SINCE:-}" ] && ! [[ "$SINCE" =~ ^[0-9]{8}$ ]]; then
    echo "Error: SINCE must be a date as YYYYMMDD, got '$SINCE'" >&2
    exit 2
fi

mkdir -p "$LOCAL_RT_DIR" "$LOCAL_STATIC_DIR"

# Resolve SSH key: prefer base64 env var (for CI/secret stores), fall back to file path
KEY_FILE=""
CLEANUP_KEY=0

if [ -n "${ORACLE_SSH_KEY:-}" ]; then
    KEY_FILE="$(mktemp)"
    echo "$ORACLE_SSH_KEY" | base64 -d > "$KEY_FILE"
    chmod 600 "$KEY_FILE"
    CLEANUP_KEY=1
elif [ -n "${ORACLE_SSH_KEY_PATH:-}" ]; then
    KEY_FILE="$ORACLE_SSH_KEY_PATH"
else
    echo "Error: set ORACLE_SSH_KEY (base64) or ORACLE_SSH_KEY_PATH" >&2
    exit 1
fi

WANTED_FILE=""
cleanup() {
    if [ "$CLEANUP_KEY" -eq 1 ]; then
        rm -f "$KEY_FILE"
    fi
    rm -f "${WANTED_FILE:-}"
    return 0
}
trap cleanup EXIT

# Keepalives turn a dropped connection into an error within about two
# minutes, where a silent one would leave rsync waiting forever.
SSH_OPTS="-i $KEY_FILE -o StrictHostKeyChecking=no -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=4"

# FETCH_TEST_LOCAL=1: rsync local paths directly (test hook, no ssh).
if [ "${FETCH_TEST_LOCAL:-0}" = "1" ]; then
    RSYNC_E=()
    REMOTE_PREFIX=""
else
    RSYNC_E=(-e "ssh $SSH_OPTS")
    REMOTE_PREFIX="${ORACLE_USER}@${ORACLE_HOST}:"
fi

if [ -n "${COLLECTOR_DATA_DIR:-}" ]; then
    # ── v3 per-agency layout ──────────────────────────────────────────────
    # COLLECTOR_DATA_DIR points at /home/opc/collector/data on the remote.
    # AGENCY_IDS defaults to ids parsed from agencies.csv rows with a feed_url.
    AGENCY_IDS="${AGENCY_IDS:-$(awk -F, 'NR>1 && $3 != "" {print $1}' "$(dirname "$0")/../agencies.csv" | tr '\n' ' ')}"
    # The collector holds only the agencies its own agencies.tsv polls, so an id
    # from agencies.csv with no directory there is skipped, not failed, and is
    # appended to $SKIPPED_AGENCIES_FILE when the caller names one. An RT fetch
    # that fails for an agency it does hold is recorded, and the rest still run
    # before the script exits nonzero.
    if ! COLLECTED="$(rsync --list-only ${RSYNC_E[@]+"${RSYNC_E[@]}"} "${REMOTE_PREFIX}${COLLECTOR_DATA_DIR}/" \
        | awk '$1 ~ /^d/ && $NF != "." { print $NF }')"; then
        echo "Error: could not list $COLLECTOR_DATA_DIR on the collector" >&2
        exit 1
    fi
    HELD=0
    for id in $AGENCY_IDS; do
        if printf '%s\n' "$COLLECTED" | grep -qx "$id"; then HELD=1; fi
    done
    if [ "$HELD" -eq 0 ]; then
        # Every requested id missing means a wrong COLLECTOR_DATA_DIR far more
        # often than a collector polling none of them.
        echo "Error: $COLLECTOR_DATA_DIR holds none of the agencies $AGENCY_IDS; is COLLECTOR_DATA_DIR right?" >&2
        exit 1
    fi
    FAILED=""
    for id in $AGENCY_IDS; do
        if ! printf '%s\n' "$COLLECTED" | grep -qx "$id"; then
            echo "==> [a$id] not collected on the VM — skipping"
            if [ -n "${SKIPPED_AGENCIES_FILE:-}" ]; then echo "$id" >> "$SKIPPED_AGENCIES_FILE"; fi
            continue
        fi
        echo "==> [a$id] RT archives${SINCE:+ from $SINCE}"
        mkdir -p "$LOCAL_RT_DIR/$id" "$LOCAL_STATIC_DIR/$id"
        if [ -n "${SINCE:-}" ]; then
            # Day archives are named YYYYMMDD.tar.gz, so the name orders them.
            WANTED_FILE="$(mktemp)"
            if rsync --list-only ${RSYNC_E[@]+"${RSYNC_E[@]}"} "${REMOTE_PREFIX}${COLLECTOR_DATA_DIR}/$id/rt/" \
                | awk -v since="$SINCE" '{ n = $NF } n ~ /^[0-9]{8}\.tar\.gz$/ && substr(n, 1, 8) >= since { print n }' \
                > "$WANTED_FILE"; then
                if [ -s "$WANTED_FILE" ]; then
                    rsync -az --progress ${RSYNC_E[@]+"${RSYNC_E[@]}"} --files-from="$WANTED_FILE" \
                        "${REMOTE_PREFIX}${COLLECTOR_DATA_DIR}/$id/rt/" \
                        "$LOCAL_RT_DIR/$id/" \
                        || FAILED="$FAILED $id"
                fi
            else
                FAILED="$FAILED $id"
            fi
            rm -f "$WANTED_FILE"
        else
            rsync -az --progress ${RSYNC_E[@]+"${RSYNC_E[@]}"} \
                --include="*.tar.gz" --exclude="*" \
                "${REMOTE_PREFIX}${COLLECTOR_DATA_DIR}/$id/rt/" \
                "$LOCAL_RT_DIR/$id/" \
                || FAILED="$FAILED $id"
        fi
        echo "==> [a$id] static archives"
        rsync -az --progress ${RSYNC_E[@]+"${RSYNC_E[@]}"} \
            --exclude="latest.zip" --include="*.zip" --exclude="*" \
            "${REMOTE_PREFIX}${COLLECTOR_DATA_DIR}/$id/static/" \
            "$LOCAL_STATIC_DIR/$id/" \
            || echo "  (no static for agency $id — skipping)"
    done
    if [ -n "$FAILED" ]; then
        echo "RT fetch failed for agencies:$FAILED" >&2
        exit 1
    fi
else
    # ── legacy flat layout (unchanged behavior) ───────────────────────────
    if [ -n "${SINCE:-}" ]; then
        echo "  (SINCE applies to the per-agency layout only; fetching every archived day)" >&2
    fi
    echo "==> Fetching RT archives from ${REMOTE_PREFIX}${ORACLE_RT_DIR}/"
    rsync -az --progress ${RSYNC_E[@]+"${RSYNC_E[@]}"} \
        --include="*.tar.gz" --exclude="*" \
        "${REMOTE_PREFIX}${ORACLE_RT_DIR}/" \
        "$LOCAL_RT_DIR/"

    echo "==> Fetching static archives from ${REMOTE_PREFIX}${ORACLE_STATIC_DIR}/"
    rsync -az --progress ${RSYNC_E[@]+"${RSYNC_E[@]}"} \
        --include="*.zip" --exclude="*" \
        "${REMOTE_PREFIX}${ORACLE_STATIC_DIR}/" \
        "$LOCAL_STATIC_DIR/" \
        || echo "  (no static dir on remote — skipping)"
fi

echo "==> Done"
