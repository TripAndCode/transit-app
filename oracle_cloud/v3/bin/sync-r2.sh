#!/usr/bin/env bash
# Mirror this collector's own data/<id>/{rt,static} straight to Cloudflare R2
# (S3-compatible API) — replaces the old two-hop "workstation mirror"
# (rsync down to a workstation via scripts/fetch_archives.sh, then
# scripts/sync_archives_to_r2.sh) with a direct upload from the VM that
# actually collects the data. Same bucket layout as that workstation
# mirror (rt/<id>/, static/<id>/), so it continues the existing R2 archive
# rather than starting a parallel one. `aws s3 sync` only transfers
# new/changed objects, so reruns are cheap and idempotent.
#
# Required env (set in /etc/environment — cron doesn't source ~/.bashrc):
#   OBJECT_STORE_ENDPOINT, OBJECT_STORE_BUCKET,
#   OBJECT_STORE_ACCESS_KEY_ID, OBJECT_STORE_SECRET_ACCESS_KEY
set -euo pipefail

: "${OBJECT_STORE_ENDPOINT:?OBJECT_STORE_ENDPOINT is required}"
: "${OBJECT_STORE_BUCKET:?OBJECT_STORE_BUCKET is required}"
: "${OBJECT_STORE_ACCESS_KEY_ID:?OBJECT_STORE_ACCESS_KEY_ID is required}"
: "${OBJECT_STORE_SECRET_ACCESS_KEY:?OBJECT_STORE_SECRET_ACCESS_KEY is required}"

export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
AWS="${AWS_CLI:-aws}"

for rt in "$BASE_DIR"/data/*/rt; do
    [ -d "$rt" ] || continue
    id=$(basename "$(dirname "$rt")")
    "$AWS" s3 sync "$rt" "s3://$OBJECT_STORE_BUCKET/rt/$id" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" \
        --exclude '*' --include '*.tar.gz' --only-show-errors
    echo "[a$id] rt synced"
done

for sdir in "$BASE_DIR"/data/*/static; do
    [ -d "$sdir" ] || continue
    id=$(basename "$(dirname "$sdir")")
    "$AWS" s3 sync "$sdir" "s3://$OBJECT_STORE_BUCKET/static/$id" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" \
        --exclude '*' --include 'gtfs_static_*.zip' --only-show-errors
    echo "[a$id] static synced"
done

echo "==> sync-r2 complete"
