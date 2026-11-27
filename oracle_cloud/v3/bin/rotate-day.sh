#!/usr/bin/env bash
# Tar + remove every CLOSED UTC day dir for every agency, both its TripUpdate
# samples (rt/) and, for an agency with a VehiclePositions feed, its vp/
# samples. Idempotent: already-tarred days are skipped; today's live dir is
# never touched. An agency with no vp/ directory is skipped there.
set -euo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
today=$(date -u +%Y%m%d)

for kind_dir in "$BASE_DIR"/data/*/rt "$BASE_DIR"/data/*/vp; do
    [ -d "$kind_dir" ] || continue
    for dir in "$kind_dir"/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/; do
        [ -d "$dir" ] || continue
        day=$(basename "$dir")
        [ "$day" = "$today" ] && continue
        tarball="$kind_dir/$day.tar.gz"
        if [ ! -f "$tarball" ]; then
            nice -n 15 tar -czf "$tarball.part" -C "$kind_dir" "$day"
            mv "$tarball.part" "$tarball"
        fi
        rm -rf "$dir"
        echo "rotated $tarball"
    done
done
