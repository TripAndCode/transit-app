#!/usr/bin/env bash
# Enforce the rolling retention window.
#   RT tarballs:  delete *.tar.gz older than RETENTION_DAYS (default 90).
#   Static zips:  delete gtfs_static_*.zip older than STATIC_RETENTION_DAYS
#                 (default 365) EXCEPT the current latest.zip target.
# Live day dirs are never touched (only *.tar.gz files are matched).
set -euo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
RETENTION_DAYS="${RETENTION_DAYS-90}"
STATIC_RETENTION_DAYS="${STATIC_RETENTION_DAYS-365}"

# Reject non-numeric / empty retention windows before they reach find (per var).
case "$RETENTION_DAYS" in *[!0-9]*|'') echo "RETENTION_DAYS must be a positive integer" >&2; exit 64;; esac
case "$STATIC_RETENTION_DAYS" in *[!0-9]*|'') echo "STATIC_RETENTION_DAYS must be a positive integer" >&2; exit 64;; esac

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
