#!/usr/bin/env bash
# End-to-end Oracle-to-R2 freshness and integrity check.
#
# sync-r2.sh's own success marker only proves `aws s3 sync` returned exit 0
# for every agency -- it says nothing about whether the objects that landed
# in R2 are actually recent, non-empty, or byte-identical to what this VM
# holds locally. A sync that races an overlapping write, an R2-side quota
# error masked by a retry, or a stale replica behind R2's own eventual
# consistency would all still leave that marker looking fresh. This script
# instead lists each configured agency's actual R2 objects and checks them
# directly:
#   * RT       — at least one rt/<id>/*.tar.gz object exists, the newest one
#                is younger than R2_RT_MAX_STALE_DAYS, is at least
#                MIN_RT_OBJECT_BYTES, and (when the same file is still on
#                local disk, i.e. not yet pruned) matches its local byte size
#                exactly.
#   * static   — at least one static/<id>/gtfs_static_*.zip object exists,
#                the one matching this agency's current local latest.zip
#                target is actually present in R2, is at least
#                MIN_STATIC_OBJECT_BYTES, and matches that local file's byte
#                size exactly.
# An agency with no static_url configured (its static GTFS is collected
# off-VM) is skipped for the static check, matching health-check.sh.
#
# Byte-size equality is the integrity check here rather than a checksum:
# `aws s3 sync` transfers whole objects, so a truncated or corrupted upload
# almost always changes the size, and comparing sizes needs no extra API
# calls (a checksum would need `s3api head-object` per file and is not
# reliable against multipart-uploaded objects' ETags anyway).
#
# One agency's problem must not hide behind another's, so problems are
# collected and turned into a nonzero exit at the end (which cron-wrap.sh
# turns into an alert) rather than aborting the loop, matching sync-r2.sh
# and static-fetch.sh.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"

: "${OBJECT_STORE_ENDPOINT:?OBJECT_STORE_ENDPOINT is required}"
: "${OBJECT_STORE_BUCKET:?OBJECT_STORE_BUCKET is required}"
: "${OBJECT_STORE_ACCESS_KEY_ID:?OBJECT_STORE_ACCESS_KEY_ID is required}"
: "${OBJECT_STORE_SECRET_ACCESS_KEY:?OBJECT_STORE_SECRET_ACCESS_KEY is required}"

export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="${AGENCIES_TSV:-$BASE_DIR/etc/agencies.tsv}"
AWS="${AWS_CLI:-aws}"
# Generous vs. the daily sync/rotate cadence: rotate-day.sh only tars
# yesterday's (already-closed) day, so right after a normal sync the newest
# rt/<id>/ object is well under a day old. Two days absorbs one missed or
# delayed cron tick without alerting on it.
R2_RT_MAX_STALE_DAYS="${R2_RT_MAX_STALE_DAYS-2}"
# A GTFS-RT tarball holding a whole day's samples is never legitimately tiny;
# default matches health-check.sh's MIN_RT_BYTES floor of "unambiguously
# broken", not a real expectation of daily volume.
MIN_RT_OBJECT_BYTES="${MIN_RT_OBJECT_BYTES-1}"
MIN_STATIC_OBJECT_BYTES="${MIN_STATIC_OBJECT_BYTES-1024}"

for var in R2_RT_MAX_STALE_DAYS MIN_RT_OBJECT_BYTES MIN_STATIC_OBJECT_BYTES; do
    value="${!var}"
    case "$value" in
        ''|*[!0-9]*)
            echo "verify-r2.sh: $var must be a non-negative integer, got '$value'" >&2
            exit 64
            ;;
    esac
done

if [ ! -f "$TSV" ]; then
    echo "verify-r2.sh: agencies roster $TSV is missing" >&2
    exit 64
fi

# epoch_from_ts "YYYY-MM-DD HH:MM:SS" (as printed by `aws s3 ls`, always UTC)
# -> epoch seconds. GNU `date -d` first, then BSD/macOS `date -j -f`.
epoch_from_ts() {
    date -u -d "$1" +%s 2>/dev/null || date -u -j -f "%Y-%m-%d %H:%M:%S" "$1" +%s 2>/dev/null
}

# r2_list <prefix> — one line per object under s3://$OBJECT_STORE_BUCKET/<prefix>,
# as "<epoch>\t<size>\t<key>", oldest first. Empty (no output) when the
# prefix has no objects or the call fails; callers treat that as "no objects".
r2_list() {
    local prefix="$1" d t size key epoch
    "$AWS" s3 ls "s3://$OBJECT_STORE_BUCKET/$prefix" --recursive \
        --endpoint-url "$OBJECT_STORE_ENDPOINT" 2>/dev/null |
    while read -r d t size key; do
        [ -n "${key:-}" ] || continue
        epoch=$(epoch_from_ts "$d $t") || continue
        printf '%s\t%s\t%s\n' "$epoch" "$size" "$key"
    done | sort -n
}

NOW=$(date -u +%s)
failed=0
agencies=0

problem() {
    echo "verify-r2.sh: PROBLEM $1" >&2
    failed=1
}

# `|| [ -n "$row" ]` processes a final row lacking a trailing newline, matching
# the other agencies.tsv readers.
while IFS= read -r row || [ -n "${row:-}" ]; do
    agency_row_is_data "$row" || continue
    split_agency_row "$row"
    agencies=$((agencies + 1))
    id="$agency_id"
    name="$agency_name"
    static="$agency_static_url"

    rt_lines=$(r2_list "rt/$id/")
    if [ -z "$rt_lines" ]; then
        problem "a$id ($name): no RT objects in R2 under rt/$id/"
    else
        newest=$(printf '%s\n' "$rt_lines" | tail -1)
        IFS=$'\t' read -r rt_epoch rt_size rt_key <<< "$newest"
        rt_age=$(( NOW - rt_epoch ))
        rt_max=$(( R2_RT_MAX_STALE_DAYS * 86400 ))
        if [ "$rt_age" -gt "$rt_max" ]; then
            problem "a$id ($name): newest R2 RT object $rt_key is $(( rt_age / 86400 ))d old (limit ${R2_RT_MAX_STALE_DAYS}d)"
        fi
        if [ "$rt_size" -lt "$MIN_RT_OBJECT_BYTES" ]; then
            problem "a$id ($name): newest R2 RT object $rt_key is $rt_size bytes (minimum $MIN_RT_OBJECT_BYTES)"
        fi
        local_rt="$BASE_DIR/data/$id/rt/$(basename "$rt_key")"
        if [ -f "$local_rt" ]; then
            local_size=$(wc -c < "$local_rt" | tr -d ' ')
            if [ "$local_size" != "$rt_size" ]; then
                problem "a$id ($name): R2 RT object $rt_key is $rt_size bytes but local $local_rt is $local_size bytes — integrity check failed"
            fi
        fi
    fi

    # An empty static_url is a real configuration (that agency's static GTFS
    # is collected off-VM), not a missing one.
    [ -n "${static:-}" ] || continue

    sdir="$BASE_DIR/data/$id/static"
    static_lines=$(r2_list "static/$id/")
    if [ -z "$static_lines" ]; then
        problem "a$id ($name): no static objects in R2 under static/$id/"
        continue
    fi

    target=$(readlink "$sdir/latest.zip" 2>/dev/null || true)
    [ -n "$target" ] && target=$(basename "$target")
    if [ -z "$target" ]; then
        # health-check.sh already alerts on a missing/dangling latest.zip;
        # nothing more this script can check without a local target to
        # compare against.
        continue
    fi

    current=""
    while IFS=$'\t' read -r line_epoch line_size line_key; do
        [ "$(basename "$line_key")" = "$target" ] || continue
        current="$line_epoch	$line_size	$line_key"
        break
    done <<< "$static_lines"
    if [ -z "$current" ]; then
        problem "a$id ($name): current static GTFS ($target) has not been mirrored to R2 yet"
        continue
    fi
    IFS=$'\t' read -r static_epoch static_size static_key <<< "$current"
    if [ "$static_size" -lt "$MIN_STATIC_OBJECT_BYTES" ]; then
        problem "a$id ($name): R2 static object $static_key is $static_size bytes (minimum $MIN_STATIC_OBJECT_BYTES)"
    fi
    local_static="$sdir/$target"
    if [ -f "$local_static" ]; then
        local_size=$(wc -c < "$local_static" | tr -d ' ')
        if [ "$local_size" != "$static_size" ]; then
            problem "a$id ($name): R2 static object $static_key is $static_size bytes but local $local_static is $local_size bytes — integrity check failed"
        fi
    fi
done < "$TSV"

if [ "$agencies" -eq 0 ]; then
    echo "verify-r2.sh: no agencies configured in $TSV — nothing to verify" >&2
    exit 1
fi

if [ "$failed" -eq 1 ]; then
    echo "==> verify-r2 completed WITH FAILURES — see above" >&2
    exit 1
fi
echo "==> verify-r2 complete — $agencies agencies checked, R2 fresh and intact"
