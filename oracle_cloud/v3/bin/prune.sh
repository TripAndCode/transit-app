#!/usr/bin/env bash
# Enforce the rolling retention window.
#   RT tarballs:  delete *.tar.gz older than RETENTION_DAYS (default 90).
#   Static zips:  delete gtfs_static_*.zip older than STATIC_RETENTION_DAYS
#                 (default 365) EXCEPT the current latest.zip target.
# Live day dirs are never touched (only *.tar.gz files are matched).
#
# Refuses to run at all unless sync-r2.sh's success marker is fresh --
# deleting local data that was never confirmed mirrored to R2 would be
# permanent, unrecoverable loss. This turns "sync-r2.sh mirrors everything
# before prune runs" from an assumption documented only in crontab.snippet's
# comment into something this script actually checks.
set -euo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
RETENTION_DAYS="${RETENTION_DAYS-90}"
STATIC_RETENTION_DAYS="${STATIC_RETENTION_DAYS-365}"
OK_MARKER="${SYNC_R2_OK_MARKER:-$BASE_DIR/.sync-r2.last-ok}"
# Generous vs. sync-r2.sh's daily cadence -- catches a real multi-day outage
# (e.g. expired R2 credentials) well before RETENTION_DAYS could delete
# anything never actually mirrored, without false-alarming on one bad night.
MAX_STALE_DAYS="${SYNC_R2_MAX_STALE_DAYS-3}"

# Reject non-numeric / empty retention windows before they reach find (per var).
case "$RETENTION_DAYS" in *[!0-9]*|'') echo "RETENTION_DAYS must be a positive integer" >&2; exit 64;; esac
case "$STATIC_RETENTION_DAYS" in *[!0-9]*|'') echo "STATIC_RETENTION_DAYS must be a positive integer" >&2; exit 64;; esac

if [ ! -f "$OK_MARKER" ]; then
    echo "prune.sh: REFUSING to run — $OK_MARKER is missing (sync-r2.sh has never completed" \
        "a fully-successful run). Local data has not been confirmed mirrored to R2." >&2
    exit 65
fi
marker_epoch=$(date -r "$OK_MARKER" +%s 2>/dev/null || stat -f %m "$OK_MARKER" 2>/dev/null)
stale_after=$(( MAX_STALE_DAYS * 86400 ))
if [ $(( $(date -u +%s) - marker_epoch )) -gt "$stale_after" ]; then
    echo "prune.sh: REFUSING to run — $OK_MARKER is more than $MAX_STALE_DAYS day(s) old." \
        "sync-r2.sh has not succeeded recently; check its cron.log output before" \
        "assuming local data is safe to delete." >&2
    exit 65
fi

for rt in "$BASE_DIR"/data/*/rt; do
    [ -d "$rt" ] || continue
    find "$rt" -maxdepth 1 -name '*.tar.gz' -type f -mtime "+$RETENTION_DAYS" -print -delete \
        | sed 's/^/pruned /'
done

for sdir in "$BASE_DIR"/data/*/static; do
    [ -d "$sdir" ] || continue
    keep=$(readlink "$sdir/latest.zip" 2>/dev/null || true)
    # Compare on basename so relative or absolute link targets both match.
    [ -n "$keep" ] && keep=$(basename "$keep")
    find "$sdir" -maxdepth 1 -name 'gtfs_static_*.zip' -type f -mtime "+$STATIC_RETENTION_DAYS" -print \
        | while read -r f; do
            [ "$(basename "$f")" = "$keep" ] && continue
            rm -f "$f"
            echo "pruned $f"
        done
done
