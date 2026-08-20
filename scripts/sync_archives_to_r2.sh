#!/usr/bin/env bash
# Mirror local raw_archives/raw_archives_static up to S3-compatible object
# storage (Cloudflare R2), so the Oracle VM's disk doesn't have to hold
# long-term history. Run after `make fetch`. `aws s3 sync` only transfers
# new/changed objects, so reruns are cheap and idempotent.
#
# Required env (see .env.example):
#   OBJECT_STORE_ENDPOINT, OBJECT_STORE_BUCKET,
#   OBJECT_STORE_ACCESS_KEY_ID, OBJECT_STORE_SECRET_ACCESS_KEY
set -euo pipefail

: "${OBJECT_STORE_ENDPOINT:?OBJECT_STORE_ENDPOINT is required}"
: "${OBJECT_STORE_BUCKET:?OBJECT_STORE_BUCKET is required}"
: "${OBJECT_STORE_ACCESS_KEY_ID:?OBJECT_STORE_ACCESS_KEY_ID is required}"
: "${OBJECT_STORE_SECRET_ACCESS_KEY:?OBJECT_STORE_SECRET_ACCESS_KEY is required}"

export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

LOCAL_RT_DIR="${LOCAL_RT_DIR:-$(dirname "$0")/../raw_archives}"
LOCAL_STATIC_DIR="${LOCAL_STATIC_DIR:-$(dirname "$0")/../raw_archives_static}"

for dir in "$LOCAL_RT_DIR"/*/; do
    [ -d "$dir" ] || continue
    id=$(basename "$dir")
    aws s3 sync "$dir" "s3://$OBJECT_STORE_BUCKET/rt/$id" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" \
        --exclude '*' --include '*.tar.gz' --only-show-errors
    echo "[a$id] rt synced"
done

# Legacy flat layout (no agency subdir) — pre-v3 fetches.
if compgen -G "$LOCAL_RT_DIR"/*.tar.gz > /dev/null; then
    aws s3 sync "$LOCAL_RT_DIR" "s3://$OBJECT_STORE_BUCKET/rt/_legacy" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" \
        --exclude '*' --include '*.tar.gz' --only-show-errors
    echo "legacy rt synced"
fi

for dir in "$LOCAL_STATIC_DIR"/*/; do
    [ -d "$dir" ] || continue
    id=$(basename "$dir")
    aws s3 sync "$dir" "s3://$OBJECT_STORE_BUCKET/static/$id" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" \
        --exclude '*' --include 'gtfs_static_*.zip' --only-show-errors
    echo "[a$id] static synced"
done

echo "==> sync complete"
