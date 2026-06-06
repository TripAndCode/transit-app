#!/usr/bin/env bash
# Pull GTFS-RT archives (tar.gz) and static ZIPs from the Oracle Cloud collection server.
# Does NOT crawl the GTFS website — that runs separately on the remote server.
#
# Required env vars:
#   ORACLE_HOST      IP or hostname of the collection server (e.g. 64.110.114.101)
#   ORACLE_USER      SSH user (e.g. opc)
#   ORACLE_SSH_KEY   Base64-encoded private key  — OR —
#   ORACLE_SSH_KEY_PATH  Path to private key file (used if ORACLE_SSH_KEY is not set)
#
# Optional:
#   ORACLE_RT_DIR    Remote RT archive directory   (default: /home/opc/app/transportation_analysis/archive)
#   ORACLE_STATIC_DIR Remote static archive dir    (default: /home/opc/app/transportation_analysis/static_archive)
#   LOCAL_RT_DIR     Local destination for RT      (default: ./raw_archives)
#   LOCAL_STATIC_DIR Local destination for static  (default: ./raw_archives_static)

set -euo pipefail

ORACLE_HOST="${ORACLE_HOST:?ORACLE_HOST is required}"
ORACLE_USER="${ORACLE_USER:-opc}"
ORACLE_RT_DIR="${ORACLE_RT_DIR:-/home/opc/app/transportation_analysis/archive}"
ORACLE_STATIC_DIR="${ORACLE_STATIC_DIR:-/home/opc/app/transportation_analysis/static_archive}"
LOCAL_RT_DIR="${LOCAL_RT_DIR:-$(dirname "$0")/../raw_archives}"
LOCAL_STATIC_DIR="${LOCAL_STATIC_DIR:-$(dirname "$0")/../raw_archives_static}"

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

cleanup() {
    if [ "$CLEANUP_KEY" -eq 1 ]; then
        rm -f "$KEY_FILE"
    fi
    return 0
}
trap cleanup EXIT

SSH_OPTS="-i $KEY_FILE -o StrictHostKeyChecking=no -o BatchMode=yes"

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
    for id in $AGENCY_IDS; do
        echo "==> [a$id] RT archives"
        mkdir -p "$LOCAL_RT_DIR/$id" "$LOCAL_STATIC_DIR/$id"
        rsync -az --progress ${RSYNC_E[@]+"${RSYNC_E[@]}"} \
            --include="*.tar.gz" --exclude="*" \
            "${REMOTE_PREFIX}${COLLECTOR_DATA_DIR}/$id/rt/" \
            "$LOCAL_RT_DIR/$id/"
        echo "==> [a$id] static archives"
        rsync -az --progress ${RSYNC_E[@]+"${RSYNC_E[@]}"} \
            --include="*.zip" --exclude="latest.zip" --exclude="*" \
            "${REMOTE_PREFIX}${COLLECTOR_DATA_DIR}/$id/static/" \
            "$LOCAL_STATIC_DIR/$id/" \
            || echo "  (no static for agency $id — skipping)"
    done
else
    # ── legacy flat layout (unchanged behavior) ───────────────────────────
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
