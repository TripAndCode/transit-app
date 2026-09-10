#!/usr/bin/env bash
# Enforce a bounded retention window on the Cloudflare R2 objects sync-r2.sh
# mirrors. R2 is the durable long-term archive -- local prune.sh's window is
# far shorter precisely because R2 was assumed to keep everything -- so these
# defaults are generous: they exist only to bound storage growth/cost the way
# an R2 lifecycle rule would, not to churn data on local prune.sh's cadence.
#   RT tarballs:  delete rt/<id>/*.tar.gz objects older than R2_RT_RETENTION_DAYS.
#   Static zips:  delete static/<id>/gtfs_static_*.zip objects older than
#                 R2_STATIC_RETENTION_DAYS, EXCEPT the object matching each
#                 agency's current local latest.zip target (mirrors local
#                 prune.sh's own "never delete the live target" rule).
#
# Refuses to run at all unless sync-r2.sh's success marker is fresh, for the
# same reason local prune.sh does: deleting R2 objects before a recent
# successful sync is confirmed risks deleting the only remaining copy of
# something local pruning (or a local disk failure) already removed.
#
# One object's delete failure must not abort the rest of the run -- collected
# and turned into a nonzero exit at the end instead, matching sync-r2.sh.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="${AGENCIES_TSV:-$BASE_DIR/etc/agencies.tsv}"
AWS="${AWS_CLI:-aws}"
OK_MARKER="${SYNC_R2_OK_MARKER:-$BASE_DIR/.sync-r2.last-ok}"
MAX_STALE_DAYS="${SYNC_R2_MAX_STALE_DAYS-3}"
R2_RT_RETENTION_DAYS="${R2_RT_RETENTION_DAYS-1825}"
R2_STATIC_RETENTION_DAYS="${R2_STATIC_RETENTION_DAYS-3650}"

: "${OBJECT_STORE_ENDPOINT:?OBJECT_STORE_ENDPOINT is required}"
: "${OBJECT_STORE_BUCKET:?OBJECT_STORE_BUCKET is required}"
: "${OBJECT_STORE_ACCESS_KEY_ID:?OBJECT_STORE_ACCESS_KEY_ID is required}"
: "${OBJECT_STORE_SECRET_ACCESS_KEY:?OBJECT_STORE_SECRET_ACCESS_KEY is required}"

export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

for var in R2_RT_RETENTION_DAYS R2_STATIC_RETENTION_DAYS MAX_STALE_DAYS; do
    value="${!var}"
    case "$value" in
        ''|*[!0-9]*|0)
            echo "prune-r2.sh: $var must be a positive integer, got '$value'" >&2
            exit 64
            ;;
    esac
done

if [ ! -f "$OK_MARKER" ]; then
    echo "prune-r2.sh: REFUSING to run — $OK_MARKER is missing (sync-r2.sh has never" \
        "completed a fully-successful run). R2 objects have not been confirmed" \
        "to reflect a complete local mirror." >&2
    exit 65
fi
if ! marker_epoch=$(date -u -r "$OK_MARKER" +%s 2>/dev/null || stat -f %m "$OK_MARKER" 2>/dev/null); then
    echo "prune-r2.sh: REFUSING to run — could not read $OK_MARKER's mtime (neither" \
        "'date -r' nor 'stat -f' worked on this platform)." >&2
    exit 65
fi
stale_after=$(( MAX_STALE_DAYS * 86400 ))
if [ $(( $(date -u +%s) - marker_epoch )) -gt "$stale_after" ]; then
    echo "prune-r2.sh: REFUSING to run — $OK_MARKER is more than $MAX_STALE_DAYS day(s) old." \
        "sync-r2.sh has not succeeded recently; check its cron.log output before" \
        "assuming R2 is safe to prune." >&2
    exit 65
fi

if [ ! -f "$TSV" ]; then
    echo "prune-r2.sh: agencies roster $TSV is missing" >&2
    exit 64
fi

# epoch_from_ts "YYYY-MM-DD HH:MM:SS" (as printed by `aws s3 ls`, always UTC)
# -> epoch seconds. GNU `date -d` first, then BSD/macOS `date -j -f`.
epoch_from_ts() {
    date -u -d "$1" +%s 2>/dev/null || date -u -j -f "%Y-%m-%d %H:%M:%S" "$1" +%s 2>/dev/null
}

NOW=$(date -u +%s)
failed=0

# prune_prefix <prefix> <retention_days> <keep-basename-or-empty>
# Deletes every object under s3://$OBJECT_STORE_BUCKET/<prefix> older than
# <retention_days>, except one whose basename equals <keep-basename-or-empty>.
prune_prefix() {
    local prefix="$1" retention="$2" keep="$3" cutoff d t size key epoch
    cutoff=$(( NOW - retention * 86400 ))
    while read -r d t size key; do
        [ -n "${key:-}" ] || continue
        if [ -n "$keep" ] && [ "$(basename "$key")" = "$keep" ]; then
            continue
        fi
        epoch=$(epoch_from_ts "$d $t") || continue
        [ "$epoch" -lt "$cutoff" ] || continue
        if "$AWS" s3 rm "s3://$OBJECT_STORE_BUCKET/$key" \
            --endpoint-url "$OBJECT_STORE_ENDPOINT" >/dev/null; then
            echo "pruned s3://$OBJECT_STORE_BUCKET/$key"
        else
            echo "prune-r2.sh: FAILED to delete s3://$OBJECT_STORE_BUCKET/$key" >&2
            failed=1
        fi
    done < <("$AWS" s3 ls "s3://$OBJECT_STORE_BUCKET/$prefix" --recursive \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" 2>/dev/null)
}

# `|| [ -n "$row" ]` processes a final row lacking a trailing newline, matching
# the other agencies.tsv readers.
while IFS= read -r row || [ -n "${row:-}" ]; do
    agency_row_is_data "$row" || continue
    split_agency_row "$row"
    id="$agency_id"
    static="$agency_static_url"

    prune_prefix "rt/$id/" "$R2_RT_RETENTION_DAYS" ""

    [ -n "${static:-}" ] || continue
    keep=$(readlink "$BASE_DIR/data/$id/static/latest.zip" 2>/dev/null || true)
    [ -n "$keep" ] && keep=$(basename "$keep")
    prune_prefix "static/$id/" "$R2_STATIC_RETENTION_DAYS" "$keep"
done < "$TSV"

if [ "$failed" -eq 1 ]; then
    echo "==> prune-r2 completed WITH FAILURES — see above" >&2
    exit 1
fi
echo "==> prune-r2 complete"
