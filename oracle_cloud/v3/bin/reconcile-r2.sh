#!/usr/bin/env bash
# Reconciles Cloudflare R2's actual object inventory against the crawler
# manifest (etc/agencies.tsv) and the naming rules the rest of bin/ writes:
#   rt/<id>/<YYYYMMDD>.tar.gz             (sync-r2.sh, from rotate-day.sh)
#   static/<id>/gtfs_static_<YYYYMMDD>.zip (sync-r2.sh, from static-fetch.sh)
# prune-r2.sh already bounds storage by AGE, but only for agency ids it reads
# out of the CURRENT agencies.tsv: drop a row (an agency retired, its id
# renumbered, a roster typo fixed) and prune-r2.sh stops looking at that
# prefix forever -- those objects are never pruned by anything, growing R2
# storage/cost with no operator visibility. The same blind spot applies to
# any object whose filename does not match the naming rule above at all (a
# manual test upload, a stray multipart fragment, a renamed file) -- it may
# eventually be swept up by prune-r2.sh's age-based rm if it happens to sit
# under a still-active agency's prefix, but is never reported as the anomaly
# it is, and is never caught at all if it sits under an id no longer in the
# roster.
#
# This script lists the ENTIRE bucket once and classifies every object as
# either "expected" (rt/<id>/ or static/<id>/, id present in agencies.tsv,
# filename matching that prefix's naming rule) or "orphan" (everything else).
#
# Dry-run by default: with no --execute, it only ever REPORTS orphans, never
# deletes. Deleting requires --execute (or RECONCILE_R2_EXECUTE=1, for cron
# use without editing an argument list) AND a fresh sync-r2.sh success
# marker, the same trust gate prune-r2.sh already requires before it deletes
# anything -- a roster read mid-edit, or one that has not been refreshed
# after a real rename, would misclassify every object under the affected id.
# Right before each individual delete, the exact key is re-listed and
# reclassified from scratch: this closes the window between the initial
# bucket-wide listing and that object's own delete, during which the roster
# could have changed (the id got re-added) or the object itself could have
# changed (size differs from the initial listing -- an upload racing this
# run), either of which means the object deleted would not be the one this
# run actually confirmed was orphaned.
#
# One object's delete failure, or one skipped-for-safety recheck mismatch,
# must not abort the rest of the run -- collected and turned into a nonzero
# exit at the end, matching sync-r2.sh/prune-r2.sh/verify-r2.sh/spool-cleanup.sh.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="${AGENCIES_TSV:-$BASE_DIR/etc/agencies.tsv}"
AWS="${AWS_CLI:-aws}"
OK_MARKER="${SYNC_R2_OK_MARKER:-$BASE_DIR/.sync-r2.last-ok}"
MAX_STALE_DAYS="${SYNC_R2_MAX_STALE_DAYS-3}"
LOCK_FILE="${RECONCILE_R2_LOCK:-$BASE_DIR/reconcile-r2.lock}"

EXECUTE=0
case "${RECONCILE_R2_EXECUTE:-0}" in
    1) EXECUTE=1 ;;
esac
for arg in "$@"; do
    case "$arg" in
        --execute) EXECUTE=1 ;;
        *)
            echo "reconcile-r2.sh: unknown argument '$arg'" >&2
            exit 64
            ;;
    esac
done

# Prevent an overlapping run (e.g. a slow full-bucket listing still running
# when the next weekly cron tick fires, or a manual --execute run started
# while the report-only cron job is mid-scan) from racing this one against
# the same objects -- matches sync-r2.sh/spool-cleanup.sh's own lock.
# `flock -n` on our own fd: if another instance already holds the lock, exit
# 0 immediately rather than failing the cron job. Degrades to "no locking"
# with a warning where `flock` isn't available (macOS dev/test boxes).
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "reconcile-r2: another run is already in progress, skipping"
        exit 0
    fi
else
    echo "reconcile-r2: WARNING flock not found — running without an overlap guard" >&2
fi

: "${OBJECT_STORE_ENDPOINT:?OBJECT_STORE_ENDPOINT is required}"
: "${OBJECT_STORE_BUCKET:?OBJECT_STORE_BUCKET is required}"
: "${OBJECT_STORE_ACCESS_KEY_ID:?OBJECT_STORE_ACCESS_KEY_ID is required}"
: "${OBJECT_STORE_SECRET_ACCESS_KEY:?OBJECT_STORE_SECRET_ACCESS_KEY is required}"

export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

if [ ! -f "$TSV" ]; then
    echo "reconcile-r2.sh: agencies roster $TSV is missing" >&2
    exit 64
fi

# Digits-only doesn't rule out a leading-zero numeral like "010" -- a plain
# arithmetic context would otherwise treat that as octal (silently changing
# its value, or aborting on an invalid octal digit like "018"). Normalize to
# base-10 now so stale_after's arithmetic below only ever sees a clean
# decimal value.
case "$MAX_STALE_DAYS" in
    ''|*[!0-9]*)
        echo "reconcile-r2.sh: MAX_STALE_DAYS must be a positive integer, got" \
            "'$MAX_STALE_DAYS'" >&2
        exit 64
        ;;
esac
if [ "$((10#$MAX_STALE_DAYS))" -le 0 ]; then
    echo "reconcile-r2.sh: MAX_STALE_DAYS must be a positive integer, got" \
        "'$MAX_STALE_DAYS'" >&2
    exit 64
fi
printf -v MAX_STALE_DAYS '%d' "$((10#$MAX_STALE_DAYS))"

if [ "$EXECUTE" -eq 1 ]; then
    if [ ! -f "$OK_MARKER" ]; then
        echo "reconcile-r2.sh: REFUSING to delete — $OK_MARKER is missing (sync-r2.sh has" \
            "never completed a fully-successful run). The current R2 inventory has not" \
            "been confirmed to reflect a complete, up-to-date mirror." >&2
        exit 65
    fi
    if ! marker_epoch=$(date -u -r "$OK_MARKER" +%s 2>/dev/null || stat -f %m "$OK_MARKER" 2>/dev/null); then
        echo "reconcile-r2.sh: REFUSING to delete — could not read $OK_MARKER's mtime (neither" \
            "'date -r' nor 'stat -f' worked on this platform)." >&2
        exit 65
    fi
    stale_after=$(( MAX_STALE_DAYS * 86400 ))
    if [ $(( $(date -u +%s) - marker_epoch )) -gt "$stale_after" ]; then
        echo "reconcile-r2.sh: REFUSING to delete — $OK_MARKER is more than $MAX_STALE_DAYS" \
            "day(s) old. sync-r2.sh has not succeeded recently; check its cron.log output" \
            "before trusting this run's view of R2 to decide what is orphaned." >&2
        exit 65
    fi
fi

# build_known_ids -- (re)sets known_ids to a newline-separated set of every
# agency id currently in $TSV (including ids with no static_url configured --
# an rt/<id>/ prefix is still expected for those). Called once up front and
# again immediately before each per-object delete-time recheck, so a roster
# edit landing between the initial bucket-wide listing and that object's own
# recheck (the id got re-added) is actually reflected in is_known_id.
build_known_ids() {
    known_ids=$'\n'
    while IFS= read -r row || [ -n "${row:-}" ]; do
        agency_row_is_data "$row" || continue
        split_agency_row "$row"
        known_ids="${known_ids}${agency_id}"$'\n'
    done < "$TSV"
}
build_known_ids

is_known_id() {
    case "$known_ids" in
        *$'\n'"$1"$'\n'*) return 0 ;;
        *) return 1 ;;
    esac
}

# classify <key> -- prints "expected" or "orphan: <reason>" for one object key.
classify() {
    local key="$1" rest id name
    case "$key" in
        rt/*/*)
            rest="${key#rt/}"
            id="${rest%%/*}"
            name="${rest#*/}"
            if ! is_known_id "$id"; then
                echo "orphan: agency id '$id' is not in $TSV"
                return
            fi
            case "$name" in
                [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].tar.gz) echo "expected" ;;
                *) echo "orphan: rt filename '$name' does not match <YYYYMMDD>.tar.gz" ;;
            esac
            ;;
        static/*/*)
            rest="${key#static/}"
            id="${rest%%/*}"
            name="${rest#*/}"
            if ! is_known_id "$id"; then
                echo "orphan: agency id '$id' is not in $TSV"
                return
            fi
            case "$name" in
                gtfs_static_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].zip) echo "expected" ;;
                *) echo "orphan: static filename '$name' does not match gtfs_static_<YYYYMMDD>.zip" ;;
            esac
            ;;
        *)
            echo "orphan: key does not live under the rt/<id>/ or static/<id>/ layout"
            ;;
    esac
}

if ! listing=$("$AWS" s3 ls "s3://$OBJECT_STORE_BUCKET/" --recursive \
    --endpoint-url "$OBJECT_STORE_ENDPOINT" 2>&1); then
    echo "reconcile-r2.sh: bucket listing failed, cannot reconcile: $listing" >&2
    exit 1
fi

total=0
orphans=0
deleted=0
failed=0

while read -r d t size key; do
    [ -n "${key:-}" ] || continue
    total=$((total + 1))
    result=$(classify "$key")
    [ "$result" = "expected" ] && continue
    orphans=$((orphans + 1))
    echo "reconcile-r2.sh: ORPHAN $key ($size bytes, modified $d $t) — ${result#orphan: }"

    [ "$EXECUTE" -eq 1 ] || continue

    # A prefix listing on $key can also return a *longer* key that merely
    # starts with it (e.g. "foo.tar.gz" is a valid S3 prefix match for
    # "foo.tar.gz.tmp" too) -- so this picks out the line whose key is
    # exactly $key rather than trusting the first line returned.
    recheck=$("$AWS" s3 ls "s3://$OBJECT_STORE_BUCKET/$key" --recursive \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" 2>/dev/null)
    recheck_size=""
    while read -r _ _ rsize rkey; do
        [ "$rkey" = "$key" ] || continue
        recheck_size="$rsize"
        break
    done <<< "$recheck"
    if [ -z "$recheck_size" ]; then
        echo "reconcile-r2.sh: $key is already gone by delete time, skipping"
        continue
    fi
    if [ "$recheck_size" != "$size" ]; then
        echo "reconcile-r2.sh: $key changed size between listing and delete ($size ->" \
            "$recheck_size bytes) — skipping rather than delete something this run never" \
            "actually confirmed" >&2
        failed=1
        continue
    fi
    build_known_ids
    recheck_result=$(classify "$key")
    if [ "$recheck_result" = "expected" ]; then
        echo "reconcile-r2.sh: $key reclassified as expected on recheck (roster changed?)" \
            "— skipping"
        continue
    fi
    if "$AWS" s3 rm "s3://$OBJECT_STORE_BUCKET/$key" \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" >/dev/null; then
        echo "reconcile-r2.sh: deleted $key"
        deleted=$((deleted + 1))
    else
        echo "reconcile-r2.sh: FAILED to delete $key" >&2
        failed=1
    fi
done <<< "$listing"

if [ "$EXECUTE" -eq 0 ]; then
    if [ "$orphans" -eq 0 ]; then
        echo "==> reconcile-r2 dry-run complete — $total object(s) scanned, none orphaned"
        exit 0
    fi
    echo "==> reconcile-r2 dry-run complete — $total object(s) scanned, $orphans orphan(s)" \
        "found (no changes made; rerun with --execute to delete)" >&2
    exit 1
fi

if [ "$failed" -eq 1 ]; then
    echo "==> reconcile-r2 completed WITH FAILURES — $total object(s) scanned, $orphans" \
        "orphan(s) found, $deleted deleted, see above" >&2
    exit 1
fi
echo "==> reconcile-r2 complete — $total object(s) scanned, $orphans orphan(s) found and" \
    "deleted"
