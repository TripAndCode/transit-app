#!/usr/bin/env bash
# Fetch each agency's static GTFS zip; store ONLY when content changed
# (sha256 vs current latest.zip target). Same-day re-change overwrites
# today's file. latest.zip is a relative symlink to the newest stored zip.
#
# A per-agency .static-last-ok marker records every successful fetch, changed
# content or not: latest.zip's mtime only moves when upstream content changes,
# so it cannot distinguish a stable upstream from a fetch that stopped working.
# health-check.sh alerts on that marker going stale.
#
# One agency's failure must not hide behind the others, and must not stop them
# either, so failures are collected and turned into a nonzero exit at the end
# (which cron-wrap.sh turns into an alert) rather than aborting the loop.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="$BASE_DIR/etc/agencies.tsv"
trap 'rm -f "${tmp:-}"' EXIT INT TERM
day=$(date -u +%Y%m%d)

sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }

failed=0

# `|| [ -n "$row" ]` processes a final row lacking a trailing newline (read
# returns nonzero at EOF but still fills the field from a hand-edited TSV).
while IFS= read -r row || [ -n "$row" ]; do
    agency_row_is_data "$row" || continue
    split_agency_row "$row"
    id="$agency_id"
    static="$agency_static_url"
    # An empty static_url is a real configuration, not a missing one: that
    # agency's static GTFS is collected off-VM.
    [ -n "$static" ] || continue

    sdir="$BASE_DIR/data/$id/static"
    mkdir -p "$sdir"
    tmp=$(mktemp "$sdir/.dl.XXXXXX")

    if ! curl -sf --max-time 60 --output "$tmp" "$static"; then
        echo "[a$id] static fetch FAILED: $static" >&2
        rm -f "$tmp"
        failed=1
        continue
    fi

    # Written before the content comparison: the fetch itself is what this
    # marker attests to. A failed write is itself a failure — health-check.sh
    # would otherwise read an unwritable marker as "static-fetch has stopped".
    if ! date -u +%Y-%m-%dT%H:%M:%SZ > "$sdir/.static-last-ok"; then
        echo "[a$id] FAILED to write static success marker $sdir/.static-last-ok" >&2
        failed=1
    fi

    new_hash=$(sha "$tmp")
    old_hash=""
    [ -e "$sdir/latest.zip" ] && old_hash=$(sha "$sdir/latest.zip")

    if [ "$new_hash" = "$old_hash" ]; then
        rm -f "$tmp"
        echo "[a$id] static unchanged"
    else
        dest="$sdir/gtfs_static_$day.zip"
        mv "$tmp" "$dest"
        ln -sfn "$(basename "$dest")" "$sdir/latest.zip"
        echo "[a$id] static saved $(basename "$dest")"
    fi
done < "$TSV"

if [ "$failed" -eq 1 ]; then
    echo "==> static-fetch completed WITH FAILURES — see above" >&2
    exit 1
fi
