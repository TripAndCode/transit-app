#!/usr/bin/env bash
# Tar + remove every CLOSED UTC day dir for every agency. Idempotent:
# already-tarred days are skipped; today's live dir is never touched.
set -euo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
today=$(date -u +%Y%m%d)

for rt in "$BASE_DIR"/data/*/rt; do
    [ -d "$rt" ] || continue
    for dir in "$rt"/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/; do
        [ -d "$dir" ] || continue
        day=$(basename "$dir")
        [ "$day" = "$today" ] && continue
        tarball="$rt/$day.tar.gz"
        if [ ! -f "$tarball" ]; then
            nice -n 15 tar -czf "$tarball.part" -C "$rt" "$day"
            mv "$tarball.part" "$tarball"
        fi
        rm -rf "$dir"
        echo "rotated $tarball"
    done
done
