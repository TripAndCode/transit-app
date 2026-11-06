#!/usr/bin/env bash
# GTFS-RT VehiclePositions poller — one instance per opted-in agency.
# Usage: vp-poller.sh <agency_id>   (run via systemd template vp-poller@<id>)
# Reads its row from etc/vehicle_positions.tsv:
#   id <TAB> name <TAB> interval_sec <TAB> url
# Writes data/<id>/vp/<UTCDAY>/VehiclePosition_HHMMSS.pb atomically (.part +
# mv), with the same fetch retries as rt-poller.sh. rotate-day.sh, sync-r2.sh
# and spool-cleanup.sh archive vp/ the way they archive rt/.
#
# These samples are observed vehicle positions, collected for a bounded
# validation window so the feeds' predicted delays can be checked against
# where the buses actually were. Nothing in the app ingests them, so unlike
# rt-poller.sh this never pushes to the app, and it sends no healthchecks.io
# ping: health-check.sh pages on a VP feed that stops producing samples.
#
# An agency without a row exits 64 before creating anything, so no empty vp/
# directory appears for rotate-day.sh or sync-r2.sh to pick up.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"
# shellcheck source=poll-lib.sh
. "$SCRIPT_DIR/poll-lib.sh"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
AGENCY_ID="${1:?usage: vp-poller.sh <agency_id>}"
TSV="${VEHICLE_POSITIONS_TSV:-$BASE_DIR/etc/vehicle_positions.tsv}"

[ -f "$TSV" ] || { echo "$TSV is missing: no VehiclePositions feed is configured" >&2; exit 64; }

# String comparison, so "08" never matches row 8 and writes under data/08/.
found=0
while IFS= read -r row || [ -n "${row:-}" ]; do
    agency_row_is_data "$row" || continue
    split_vp_row "$row"
    if [ "$vp_id" = "$AGENCY_ID" ]; then
        found=1
        break
    fi
done < "$TSV"
[ "$found" -eq 1 ] || { echo "agency $AGENCY_ID not found in $TSV" >&2; exit 64; }
NAME="$vp_name"
INTERVAL="$vp_interval"
URL="$vp_url"
case "$INTERVAL" in ''|*[!0-9]*|0|0[0-9]*) echo "invalid interval '$INTERVAL' for agency $AGENCY_ID" >&2; exit 64;; esac
[ -n "$URL" ] || { echo "empty url for agency $AGENCY_ID in $TSV" >&2; exit 64; }

VP_DIR="$BASE_DIR/data/$AGENCY_ID/vp"
mkdir -p "$VP_DIR"
# Scoped to our own samples: must never reap rotate-day's <day>.tar.gz.part.
find "$VP_DIR" -name 'VehiclePosition_*.part' -type f -delete 2>/dev/null || true
trap '[ -n "${f:-}" ] && rm -f "$f.part" 2>/dev/null' EXIT
trap 'exit 143' TERM INT

echo "[a$AGENCY_ID/$NAME] start vehicle positions interval=${INTERVAL}s feed=$URL"
while true; do
    day=$(date -u +%Y%m%d)
    dir="$VP_DIR/$day"
    mkdir -p "$dir"
    f="$dir/VehiclePosition_$(date -u +%H%M%S).pb"

    if fetch_with_retry "$URL" "$f.part"; then
        mv "$f.part" "$f"
        echo "OK $(basename "$f") ($(wc -c < "$f" | tr -d ' ') bytes)"
    else
        rm -f "$f.part"
        echo "FAIL fetch $URL (4 attempts)" >&2
    fi

    sleep "$INTERVAL"
done
