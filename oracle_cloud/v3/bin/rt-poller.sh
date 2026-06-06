#!/usr/bin/env bash
# Generic GTFS-RT poller — one instance per agency.
# Usage: rt-poller.sh <agency_id>   (run via systemd template rt-poller@<id>)
# Reads its row from etc/agencies.tsv:
#   id <TAB> name <TAB> interval_sec <TAB> feed_url <TAB> static_url <TAB> ping_url
# Writes data/<id>/rt/<UTCDAY>/TripUpdate_HHMMSS.pb atomically (.part + mv).
# Sends a healthchecks.io ping after a successful fetch, at most every ~5 min.
set -euo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
AGENCY_ID="${1:?usage: rt-poller.sh <agency_id>}"
TSV="$BASE_DIR/etc/agencies.tsv"

row=$(awk -F'\t' -v id="$AGENCY_ID" '$1==id && $0 !~ /^#/ {print; exit}' "$TSV")
[ -n "$row" ] || { echo "agency $AGENCY_ID not found in $TSV" >&2; exit 64; }
IFS=$'\t' read -r _ NAME INTERVAL FEED_URL STATIC_URL PING_URL <<< "$row"
: "$STATIC_URL"  # unused here (static-fetch.sh's job); kept for column clarity
case "$INTERVAL" in ''|*[!0-9]*|0|0[0-9]*) echo "invalid interval '$INTERVAL' for agency $AGENCY_ID" >&2; exit 64;; esac

RT_DIR="$BASE_DIR/data/$AGENCY_ID/rt"
mkdir -p "$RT_DIR"
# Clean any .part left behind by a previous kill (each loop uses a fresh name).
# Scope to our own TripUpdate_*.part: must never reap rotate-day's <day>.tar.gz.part.
find "$RT_DIR" -name 'TripUpdate_*.part' -type f -delete 2>/dev/null || true
trap '[ -n "${f:-}" ] && rm -f "$f.part" 2>/dev/null' EXIT
trap 'exit 143' TERM INT

PING_EVERY=$(( 300 / INTERVAL ))
[ "$PING_EVERY" -lt 1 ] && PING_EVERY=1
i=0

echo "[a$AGENCY_ID/$NAME] start interval=${INTERVAL}s feed=$FEED_URL"
while true; do
    day=$(date -u +%Y%m%d)
    dir="$RT_DIR/$day"
    mkdir -p "$dir"
    f="$dir/TripUpdate_$(date -u +%H%M%S).pb"

    ok=0 attempt=1 backoff=2
    while [ "$attempt" -le 4 ]; do
        if curl -sf --max-time 8 --output "$f.part" "$FEED_URL"; then
            ok=1
            break
        fi
        sleep "$backoff"
        backoff=$(( backoff * 2 ))
        attempt=$(( attempt + 1 ))
    done

    if [ "$ok" -eq 1 ]; then
        mv "$f.part" "$f"
        echo "OK $(basename "$f") ($(wc -c < "$f" | tr -d ' ') bytes)"
        if [ -n "$PING_URL" ] && [ $(( i % PING_EVERY )) -eq 0 ]; then
            curl -fsS -m 5 -o /dev/null "$PING_URL" || true
        fi
    else
        rm -f "$f.part"
        echo "FAIL fetch $FEED_URL (4 attempts)" >&2
    fi

    i=$(( i + 1 ))
    sleep "$INTERVAL"
done
