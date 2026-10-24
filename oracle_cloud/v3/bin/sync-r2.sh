#!/usr/bin/env bash
# Mirror this collector's own data/<id>/{rt,static} straight to Cloudflare R2
# (S3-compatible API) — replaces the old two-hop "workstation mirror"
# (rsync down to a workstation via scripts/fetch_archives.sh, then
# scripts/sync_archives_to_r2.sh) with a direct upload from the VM that
# actually collects the data. Same bucket layout as that workstation
# mirror (rt/<id>/, static/<id>/), so it continues the existing R2 archive
# rather than starting a parallel one. `aws s3 sync` only transfers
# new/changed objects, so reruns are cheap and idempotent. This script never
# deletes anything; R2-side retention is bin/prune-r2.sh's job (it runs on
# its own, much longer, cadence since R2 is the durable long-term archive —
# see that script), and bin/verify-r2.sh independently confirms what actually
# landed in R2 is fresh and intact rather than trusting this script's own
# exit status alone.
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
# prune.sh refuses to delete unless this marker is fresh -- written ONLY on
# a fully-successful run (see the end of this script), so prune.sh's
# "R2 already has it" assumption is actually enforced, not just asserted
# in a comment. A stale/missing marker means prune.sh must not trust that
# assumption, even though nothing pages a human on that yet (see
# crontab.snippet).
OK_MARKER="${SYNC_R2_OK_MARKER:-$BASE_DIR/.sync-r2.last-ok}"

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

# The directories mirrored here are written to while this runs: rt-poller.sh
# renames TripUpdate_*.pb.part files in the open UTC day directory under rt/,
# rotate-day.sh writes <day>.tar.gz.part beside the tarballs, and
# static-fetch.sh creates and removes .dl.* downloads in static/. The AWS CLI
# stats every file under its source before applying --exclude, and a file
# that vanishes in between makes it exit nonzero even though every archive
# uploaded. So each sync reads a per-run staging directory holding only
# symlinks to the archives it mirrors. Each link is named after its target,
# and --follow-symlinks makes the CLI read the target's size, mtime and
# bytes, so the comparison and upload match a direct sync of those files.
# Archives are only ever created by an atomic rename and never rewritten.
STAGE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/sync-r2.XXXXXX") || {
    echo "sync-r2: FAILED to create a staging directory" >&2
    exit 1
}
trap 'rm -rf "$STAGE_ROOT"' EXIT

# sync_archives <src-dir> <name-glob> <s3-dest> <stage-name>
# Links every regular file directly in <src-dir> whose name matches
# <name-glob> into a fresh staging directory and syncs that to <s3-dest>.
# Nonzero when staging or the sync itself fails. An empty staging directory
# is still synced, so an unreachable R2 fails the agency either way.
sync_archives() {
    local src pattern="$2" dest="$3" stage="$STAGE_ROOT/$4" f
    # Absolute, so the links resolve from the staging directory.
    src=$(cd "$1" && pwd) || return 1
    mkdir "$stage" || return 1
    for f in "$src"/*; do
        # $pattern is matched as a glob on purpose.
        # shellcheck disable=SC2254
        case "${f##*/}" in $pattern) ;; *) continue ;; esac
        [ -f "$f" ] || continue
        ln -s "$f" "$stage/${f##*/}" || return 1
    done
    "$AWS" s3 sync "$stage" "$dest" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" --follow-symlinks \
        --exclude '*' --include "$pattern" --only-show-errors
}

failed=0
processed=0

for rt in "$BASE_DIR"/data/*/rt; do
    [ -d "$rt" ] || continue
    processed=$((processed + 1))
    id=$(basename "$(dirname "$rt")")
    if sync_archives "$rt" '*.tar.gz' "s3://$OBJECT_STORE_BUCKET/rt/$id" "rt-$id"; then
        echo "[a$id] rt synced"
    else
        echo "[a$id] rt sync FAILED" >&2
        failed=1
    fi
done

for sdir in "$BASE_DIR"/data/*/static; do
    [ -d "$sdir" ] || continue
    processed=$((processed + 1))
    id=$(basename "$(dirname "$sdir")")
    if sync_archives "$sdir" 'gtfs_static_*.zip' "s3://$OBJECT_STORE_BUCKET/static/$id" "static-$id"; then
        echo "[a$id] static synced"
    else
        echo "[a$id] static sync FAILED" >&2
        failed=1
    fi
done

# A vacuous "success" (no agency directories found at all -- e.g. a
# misconfigured COLLECTOR_BASE) must NOT write the marker: prune.sh would
# otherwise see a fresh marker and delete local data that was never
# actually looked at, let alone mirrored.
if [ "$processed" -eq 0 ]; then
    echo "sync-r2: no agency data directories found under $BASE_DIR/data" \
        "— refusing to mark success" >&2
    exit 1
fi

if [ "$failed" -eq 1 ]; then
    echo "==> sync-r2 completed WITH FAILURES — see above" >&2
    exit 1
fi

# Only touch the marker on a fully-successful run (every agency, both rt
# and static) -- a partial success must NOT refresh prune.sh's freshness
# check, or a permanently-failing single agency could still let prune.sh
# delete data for that agency forever while the marker stays "fresh" from
# everyone else's successful syncs. Check the write itself too: a failed
# write (bad path, disk full) must not be reported as "complete".
if ! date -u +%Y-%m-%dT%H:%M:%SZ > "$OK_MARKER"; then
    echo "sync-r2: FAILED to write success marker $OK_MARKER" >&2
    exit 1
fi
echo "==> sync-r2 complete"
