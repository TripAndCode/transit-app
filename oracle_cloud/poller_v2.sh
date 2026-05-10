#!/usr/bin/env bash
# Multi-agency RT poller. Reads /home/opc/app/transportation_analysis/agencies.json
# (exported from agencies.csv) and runs one fetch loop per agency in the background.
#
# agencies.json format: [{"agency_id": 1, "feed_url": "https://..."}, ...]
set -euo pipefail

BASE_DIR="/home/opc/app/transportation_analysis"
ARCHIVE_DIR="$BASE_DIR/archive"
LOG_FILE="$BASE_DIR/poller.log"
AGENCIES_JSON="$BASE_DIR/agencies.json"

INTERVAL=30
MAX_RETRIES=4
RETRY_WAIT=2

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

fetch_pb() {
    local url="$1"
    local dest="$2"
    local attempt=1
    local wait=$RETRY_WAIT
    while [ $attempt -le $MAX_RETRIES ]; do
        if curl -sf --max-time 8 --output "$dest" "$url"; then
            return 0
        fi
        sleep "$wait"
        wait=$(( wait * 2 ))
        attempt=$(( attempt + 1 ))
    done
    return 1
}

agency_loop() {
    local agency_id="$1"
    local feed_url="$2"
    local CURRENT_DAY
    CURRENT_DAY=$(date -u '+%Y%m%d')
    log "[a$agency_id] start (${INTERVAL}s, $feed_url)"
    while true; do
        local NEW_DAY
        NEW_DAY=$(date -u '+%Y%m%d')
        if [ "$NEW_DAY" != "$CURRENT_DAY" ]; then
            local OLD_DIR="$ARCHIVE_DIR/$agency_id/$CURRENT_DAY"
            local OLD_TAR="$ARCHIVE_DIR/$agency_id/$CURRENT_DAY.tar.gz"
            if [ -d "$OLD_DIR" ]; then
                log "[a$agency_id] tar+rm $CURRENT_DAY"
                nice -n 15 tar -czf "$OLD_TAR" -C "$ARCHIVE_DIR/$agency_id" "$CURRENT_DAY"
                rm -rf "$OLD_DIR"
            fi
            CURRENT_DAY="$NEW_DAY"
        fi
        local DIR="$ARCHIVE_DIR/$agency_id/$CURRENT_DAY"
        local FILE="TripUpdate_$(date -u '+%H%M%S').pb"
        mkdir -p "$DIR"
        if fetch_pb "$feed_url" "$DIR/$FILE"; then
            local SIZE
            SIZE=$(wc -c < "$DIR/$FILE")
            log "[a$agency_id] OK $FILE ($SIZE bytes)"
        else
            log "[a$agency_id] FAIL"
            rm -f "$DIR/$FILE"
        fi
        sleep "$INTERVAL"
    done
}

mkdir -p "$ARCHIVE_DIR"
[ -f "$AGENCIES_JSON" ] || { log "no $AGENCIES_JSON"; exit 1; }

# Spawn one loop per agency
while IFS=$'\t' read -r AID URL; do
    [ -z "$AID" ] && continue
    agency_loop "$AID" "$URL" &
done < <(python3 -c '
import json, sys
data = json.load(open("'"$AGENCIES_JSON"'"))
for a in data:
    if a.get("feed_url"):
        print(f"{a[\"agency_id\"]}\t{a[\"feed_url\"]}")
')

wait
