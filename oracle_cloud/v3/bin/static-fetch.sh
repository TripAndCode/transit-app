#!/usr/bin/env bash
# Fetch each agency's static GTFS zip; store ONLY when content changed
# (sha256 vs current latest.zip target). Same-day re-change overwrites
# today's file. latest.zip is a relative symlink to the newest stored zip.
set -euo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="$BASE_DIR/etc/agencies.tsv"
day=$(date -u +%Y%m%d)

sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }

while IFS=$'\t' read -r id name interval feed static ping; do
    case "$id" in ''|\#*) continue ;; esac
    [ -n "${static:-}" ] || continue
    : "$name" "$interval" "$feed" "$ping"

    sdir="$BASE_DIR/data/$id/static"
    mkdir -p "$sdir"
    tmp=$(mktemp "$sdir/.dl.XXXXXX")

    if ! curl -sf --max-time 60 --output "$tmp" "$static"; then
        echo "[a$id] static fetch FAILED: $static" >&2
        rm -f "$tmp"
        continue
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
