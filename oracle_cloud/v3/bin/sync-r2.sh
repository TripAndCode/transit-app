#!/usr/bin/env bash
# Mirror this collector's own data/<id>/{rt,static} straight to Cloudflare R2
# (S3-compatible API) — replaces the old two-hop "workstation mirror"
# (rsync down to a workstation via scripts/fetch_archives.sh, then
# scripts/sync_archives_to_r2.sh) with a direct upload from the VM that
# actually collects the data. Same bucket layout as that workstation
# mirror (rt/<id>/, static/<id>/), so it continues the existing R2 archive
# rather than starting a parallel one. `aws s3 sync` only transfers
# new/changed objects, so reruns are cheap and idempotent. R2-side objects
# are never pruned by this script or by prune.sh (which only deletes local
# files) — an accepted, unbounded-growth tradeoff at current data volumes;
# revisit with an R2 lifecycle rule if that changes.
#
# Required env (set in /etc/environment — cron doesn't source ~/.bashrc):
#   OBJECT_STORE_ENDPOINT, OBJECT_STORE_BUCKET,
#   OBJECT_STORE_ACCESS_KEY_ID, OBJECT_STORE_SECRET_ACCESS_KEY
#
# One agency's sync failure (transient R2/network error) must not silently
# skip every agency after it in the loop, since prune.sh's local deletion
# now trusts this script to have mirrored everything first — so each `aws`
# call is isolated (no bare `set -e` abort mid-loop) and failures are
# collected, reported, and turned into a nonzero exit at the end instead.
set -uo pipefail

: "${OBJECT_STORE_ENDPOINT:?OBJECT_STORE_ENDPOINT is required}"
: "${OBJECT_STORE_BUCKET:?OBJECT_STORE_BUCKET is required}"
: "${OBJECT_STORE_ACCESS_KEY_ID:?OBJECT_STORE_ACCESS_KEY_ID is required}"
: "${OBJECT_STORE_SECRET_ACCESS_KEY:?OBJECT_STORE_SECRET_ACCESS_KEY is required}"

export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
AWS="${AWS_CLI:-aws}"
LOCK_FILE="${SYNC_R2_LOCK:-$BASE_DIR/sync-r2.lock}"

# Prevent an overlapping run (e.g. a slow first-time backlog upload still
# running when the next day's cron fires) from racing this one against the
# same R2 prefixes. `flock -n` on our own fd: if another instance already
# holds the lock, exit 0 immediately rather than failing the cron job.
# `flock` is util-linux (Oracle Linux collector VM has it); macOS dev/test
# boxes don't ship it, so degrade to "no locking" there with a warning
# rather than failing outright.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "sync-r2: another run is already in progress, skipping"
        exit 0
    fi
else
    echo "sync-r2: WARNING flock not found — running without an overlap guard" >&2
fi

failed=0

for rt in "$BASE_DIR"/data/*/rt; do
    [ -d "$rt" ] || continue
    id=$(basename "$(dirname "$rt")")
    if "$AWS" s3 sync "$rt" "s3://$OBJECT_STORE_BUCKET/rt/$id" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" \
        --exclude '*' --include '*.tar.gz' --only-show-errors; then
        echo "[a$id] rt synced"
    else
        echo "[a$id] rt sync FAILED" >&2
        failed=1
    fi
done

for sdir in "$BASE_DIR"/data/*/static; do
    [ -d "$sdir" ] || continue
    id=$(basename "$(dirname "$sdir")")
    if "$AWS" s3 sync "$sdir" "s3://$OBJECT_STORE_BUCKET/static/$id" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" \
        --exclude '*' --include 'gtfs_static_*.zip' --only-show-errors; then
        echo "[a$id] static synced"
    else
        echo "[a$id] static sync FAILED" >&2
        failed=1
    fi
done

if [ "$failed" -eq 1 ]; then
    echo "==> sync-r2 completed WITH FAILURES — see above" >&2
    exit 1
fi
echo "==> sync-r2 complete"
