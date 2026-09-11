#!/usr/bin/env bash
# Idempotent local disk reclamation for the collector's spool of RT/static
# archives.
#
# prune.sh already deletes local archives, but only once two conditions both
# hold: RETENTION_DAYS (default 90) has passed, AND sync-r2.sh's global
# .sync-r2.last-ok marker is fresh. That marker proves every configured
# agency's `aws s3 sync` returned 0 on the *last* run -- it says nothing about
# whether any specific local file was actually part of that transfer, and it
# gates a 90-day-later cleanup, not an immediate one. This script instead
# re-derives, the same way verify-r2.sh does (an actual R2 listing, byte-size
# compared against the local copy), whether each specific archive is
# confirmed uploaded, and reclaims its disk the moment that is true --
# independent of prune.sh's much longer retention window. Each rt/$id/ or
# static/$id/ prefix is listed once per run and that listing is reused for
# every local file under it (matching verify-r2.sh's own r2_list), rather
# than re-listing per file. A failed or partially uploaded archive (missing
# from R2, or present with a mismatched size) is left in place for the next
# sync-r2.sh run to retry: this script only ever deletes a file it has
# itself just confirmed is byte-identical in R2.
#
# Full removal, not compaction: once a copy verified byte-identical to R2
# exists, there is nothing left locally worth compacting -- removal reclaims
# all of that file's space, which any local compaction scheme could only do
# partially.
#
# Never touches: the live (still-being-written) day directory (rotate-day.sh
# only ever produces a *.tar.gz once a day is closed, so the glob below can
# never match it), and the static latest.zip symlink target, which stays
# regardless of whether R2 also has it -- deleting the live pointer would
# break the static poller and health-check the same way prune.sh's own
# keep-latest exception exists to prevent.
#
# Bounded by an explicit disk budget (SPOOL_DISK_BUDGET_BYTES, required): once
# every confirmed-uploaded archive has been reclaimed, whatever RT/static
# archive bytes remain on local disk are measured and compared against this
# budget. Exceeding it is reported as a failure, never as a further deletion
# -- anything still on disk at that point is by definition not yet a
# confirmed-safe-to-delete upload, so the only two honest responses are "wait
# for sync-r2.sh to catch up" or "page a human," never "delete it anyway."
#
# One agency's or one file's problem must not hide behind another's or abort
# the rest of the run, matching sync-r2.sh/prune-r2.sh/verify-r2.sh.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="${AGENCIES_TSV:-$BASE_DIR/etc/agencies.tsv}"
AWS="${AWS_CLI:-aws}"
LOCK_FILE="${SPOOL_CLEANUP_LOCK:-$BASE_DIR/spool-cleanup.lock}"

: "${OBJECT_STORE_ENDPOINT:?OBJECT_STORE_ENDPOINT is required}"
: "${OBJECT_STORE_BUCKET:?OBJECT_STORE_BUCKET is required}"
: "${OBJECT_STORE_ACCESS_KEY_ID:?OBJECT_STORE_ACCESS_KEY_ID is required}"
: "${OBJECT_STORE_SECRET_ACCESS_KEY:?OBJECT_STORE_SECRET_ACCESS_KEY is required}"
# Required, not defaulted: an unbounded spool is exactly the failure mode this
# script exists to prevent, so it refuses to guess a "reasonable" cap on the
# operator's behalf.
: "${SPOOL_DISK_BUDGET_BYTES:?SPOOL_DISK_BUDGET_BYTES is required}"

export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

case "$SPOOL_DISK_BUDGET_BYTES" in
    ''|*[!0-9]*)
        echo "spool-cleanup.sh: SPOOL_DISK_BUDGET_BYTES must be a positive integer, got '$SPOOL_DISK_BUDGET_BYTES'" >&2
        exit 64
        ;;
esac
# Digits-only doesn't rule out a leading-zero numeral ("00", "010") -- forcing
# base-10 interpretation now, and normalizing the variable to its plain
# decimal form, is what keeps every later arithmetic use of it from being
# silently misread as octal (matching prune-r2.sh/verify-r2.sh).
if [ "$((10#$SPOOL_DISK_BUDGET_BYTES))" -le 0 ]; then
    echo "spool-cleanup.sh: SPOOL_DISK_BUDGET_BYTES must be a positive integer, got '$SPOOL_DISK_BUDGET_BYTES'" >&2
    exit 64
fi
printf -v SPOOL_DISK_BUDGET_BYTES '%d' "$((10#$SPOOL_DISK_BUDGET_BYTES))"

if [ ! -f "$TSV" ]; then
    echo "spool-cleanup.sh: agencies roster $TSV is missing" >&2
    exit 64
fi

# Prevent an overlapping run (e.g. a slow first-time backlog reclaim still
# running when the next day's cron fires) from racing this one against the
# same local files. Matches sync-r2.sh's own lock: `flock -n` on our own fd,
# degrading to "no locking" with a warning where flock isn't available
# (macOS dev/test boxes) rather than failing outright. Not required for
# correctness -- `rm -f` on an already-removed file is itself a no-op, so two
# overlapping runs cannot corrupt anything -- only to avoid doubling up on
# `aws s3 ls` calls.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "spool-cleanup: another run is already in progress, skipping"
        exit 0
    fi
else
    echo "spool-cleanup: WARNING flock not found — running without an overlap guard" >&2
fi

# r2_list <prefix> — echoes one line per object under
# s3://$OBJECT_STORE_BUCKET/<prefix> (raw `aws s3 ls --recursive` rows).
# Callers list a given prefix once per run and reuse the result for every
# local file under it, rather than re-listing per file.
r2_list() {
    local prefix="$1"
    "$AWS" s3 ls "s3://$OBJECT_STORE_BUCKET/$prefix" --recursive \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" 2>/dev/null
}

# lookup_size <listing> <basename> — echoes the byte size of the object in
# <listing> (as produced by r2_list) whose basename matches, and returns 0.
# Returns 1 (nothing echoed) when no such object is listed.
lookup_size() {
    local listing="$1" name="$2" d t size key
    while read -r d t size key; do
        [ -n "${key:-}" ] || continue
        if [ "$(basename "$key")" = "$name" ]; then
            printf '%s' "$size"
            return 0
        fi
    done <<< "$listing"
    return 1
}

failed=0
removed=0
remaining_bytes=0

# reclaim_or_keep <local-file> <r2-listing> <r2-prefix>
# Removes <local-file> when <r2-listing> (the already-fetched listing of
# <r2-prefix>, see r2_list) already holds a byte-identical object with the
# same basename; otherwise leaves it in place (recoverable) and adds its size
# to the running remaining_bytes total.
reclaim_or_keep() {
    local f="$1" listing="$2" prefix="$3" name local_size remote_size
    name=$(basename "$f")
    local_size=$(wc -c < "$f" | tr -d ' ')
    if remote_size=$(lookup_size "$listing" "$name"); then
        if [ "$remote_size" = "$local_size" ]; then
            if rm -f "$f"; then
                removed=$((removed + 1))
                echo "spool-cleanup: removed $f (verified ${local_size}B in R2)"
                return 0
            fi
            echo "spool-cleanup: FAILED to remove $f" >&2
            failed=1
        else
            echo "spool-cleanup: $f kept — R2 object size ($remote_size) does not match local ($local_size), a partial/corrupt upload" >&2
        fi
    else
        echo "spool-cleanup: $f kept — not yet found in R2 under $prefix"
    fi
    remaining_bytes=$((remaining_bytes + local_size))
}

# `|| [ -n "$row" ]` processes a final row lacking a trailing newline, matching
# the other agencies.tsv readers.
while IFS= read -r row || [ -n "${row:-}" ]; do
    agency_row_is_data "$row" || continue
    split_agency_row "$row"
    id="$agency_id"

    rt="$BASE_DIR/data/$id/rt"
    if [ -d "$rt" ]; then
        rt_files=$(find "$rt" -maxdepth 1 -name '*.tar.gz' -type f)
        if [ -n "$rt_files" ]; then
            rt_listing=$(r2_list "rt/$id/")
            while IFS= read -r f; do
                [ -n "$f" ] || continue
                reclaim_or_keep "$f" "$rt_listing" "rt/$id/"
            done <<< "$rt_files"
        fi
    fi

    sdir="$BASE_DIR/data/$id/static"
    if [ -d "$sdir" ]; then
        keep=$(readlink "$sdir/latest.zip" 2>/dev/null || true)
        # Compare on basename so relative or absolute link targets both match.
        [ -n "$keep" ] && keep=$(basename "$keep")
        static_files=$(find "$sdir" -maxdepth 1 -name 'gtfs_static_*.zip' -type f)
        if [ -n "$static_files" ]; then
            static_listing=$(r2_list "static/$id/")
            while IFS= read -r f; do
                [ -n "$f" ] || continue
                if [ -n "$keep" ] && [ "$(basename "$f")" = "$keep" ]; then
                    size=$(wc -c < "$f" | tr -d ' ')
                    remaining_bytes=$((remaining_bytes + size))
                    continue
                fi
                reclaim_or_keep "$f" "$static_listing" "static/$id/"
            done <<< "$static_files"
        fi
    fi
done < "$TSV"

if [ "$remaining_bytes" -gt "$SPOOL_DISK_BUDGET_BYTES" ]; then
    echo "spool-cleanup.sh: PROBLEM local spool is ${remaining_bytes} bytes, over the ${SPOOL_DISK_BUDGET_BYTES}-byte budget, after reclaiming every archive R2 has confirmed -- the rest is either still pending its first successful upload or genuinely failing to sync; investigate sync-r2.sh/verify-r2.sh before anything here can safely free more" >&2
    failed=1
fi

if [ "$failed" -eq 1 ]; then
    echo "==> spool-cleanup completed WITH FAILURES — see above ($removed archive(s) reclaimed, ${remaining_bytes} bytes remain)" >&2
    exit 1
fi
echo "==> spool-cleanup complete — $removed archive(s) reclaimed, ${remaining_bytes} bytes remain in spool (budget ${SPOOL_DISK_BUDGET_BYTES})"
