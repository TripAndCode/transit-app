#!/usr/bin/env bash
# Build one operations-status document (see scripts/ops_status.py in the main
# repo checkout) for this collector VM and write it atomically to disk.
#
# This is a pure report, not a check: unlike health-check.sh, it never pages
# anyone and its exit status reflects only "did I manage to produce a
# well-formed document", never "is the collector healthy". A stale RT feed or
# a missing R2 marker is a normal, valid THING TO REPORT (state=stale/unknown
# in the document itself), not a reason for this script to fail -- that
# distinction is what lets publish-status.sh treat "no document" and
# "document says trouble" as different problems.
#
# Rather than re-run health-check.sh's/verify-r2.sh's own checks, this reads
# the same on-disk evidence they already produce (RT sample mtimes, the
# `.static-last-ok` / `.sync-r2.last-ok` markers, verify-r2.sh's own
# `.verify-r2.last-result` marker) so the numbers here can never disagree with
# what those scripts alerted on, and so this never needs R2 credentials or a
# network call of its own.
#
# One `state` covers three independently-tracked subsystems (RT polling,
# static fetching, the R2 mirror). Each is classified on its own thresholds
# (their cadences differ by orders of magnitude) into healthy/degraded/stale/
# unknown/not_applicable, then combined by taking the worst (severity order
# failed > stale > degraded > unknown > healthy; `not_applicable` -- a
# subsystem this collector is not configured to run at all, e.g. no agency
# has a static_url -- is excluded from the comparison entirely rather than
# counted as any state). The combined document's `last_success_at` is the
# timestamp belonging to whichever subsystem produced that worst verdict, so
# it stays internally consistent with the `age_seconds` the operations-status
# contract requires.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="${AGENCIES_TSV:-$BASE_DIR/etc/agencies.tsv}"
SYNC_OK_MARKER="${SYNC_R2_OK_MARKER:-$BASE_DIR/.sync-r2.last-ok}"
VERIFY_RESULT_MARKER="${VERIFY_R2_RESULT_MARKER:-$BASE_DIR/.verify-r2.last-result}"
OUT_FILE="${ORACLE_STATUS_FILE:-$BASE_DIR/.status/oracle-crawler-status.json}"

# Reused as-is from health-check.sh/verify-r2.sh so a threshold that pages a
# human and the one that classifies this document can never silently drift
# apart into two different answers for "is this fresh".
RT_STALE_FACTOR="${RT_STALE_FACTOR-20}"
STATIC_MAX_STALE_DAYS="${STATIC_MAX_STALE_DAYS-3}"
SYNC_R2_MAX_STALE_DAYS="${SYNC_R2_MAX_STALE_DAYS-3}"
# How long ago verify-r2.sh itself must have last completed (any result) to
# still count as "recent enough to trust" -- a distinct question from
# R2_RT_MAX_STALE_DAYS, which is verify-r2.sh's own threshold for how old the
# *content* it inspected is allowed to be. Matches SYNC_R2_MAX_STALE_DAYS's
# scale since sync-r2.sh and verify-r2.sh run back-to-back on the same cadence.
VERIFY_R2_MAX_STALE_DAYS="${VERIFY_R2_MAX_STALE_DAYS-3}"

for var in RT_STALE_FACTOR STATIC_MAX_STALE_DAYS SYNC_R2_MAX_STALE_DAYS VERIFY_R2_MAX_STALE_DAYS; do
    value="${!var}"
    case "$value" in
        ''|*[!0-9]*)
            echo "status-snapshot: $var must be a non-negative integer, got '$value'" >&2
            exit 64
            ;;
    esac
    printf -v "$var" '%d' "$((10#$value))"
done

NOW=$(date -u +%s)

# mtime in epoch seconds, matching health-check.sh's own helper exactly.
file_epoch() { date -r "$1" +%s 2>/dev/null || stat -f %m "$1" 2>/dev/null; }

# epoch_to_iso <epoch seconds> -> "YYYY-MM-DDTHH:MM:SSZ" in UTC.
epoch_to_iso() {
    date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ
}

# parse_iso_epoch <"YYYY-MM-DDTHH:MM:SSZ"> -> epoch seconds, or empty on a
# blank/malformed input. GNU `date -d` parses this natively; BSD/macOS needs
# an explicit format string via `-j -f`.
parse_iso_epoch() {
    [ -n "${1:-}" ] || return 0
    date -u -d "$1" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$1" +%s 2>/dev/null || true
}

# severity_rank <state> -> integer; higher is worse. -1 for a state not
# meant to be compared (not_applicable), so callers must exclude it first
# rather than rely on it losing every comparison "by accident".
severity_rank() {
    case "$1" in
        failed) echo 4 ;;
        stale) echo 3 ;;
        degraded) echo 2 ;;
        unknown) echo 1 ;;
        healthy) echo 0 ;;
        *) echo -1 ;;
    esac
}

# classify_ratio <age_seconds * 1000 / threshold_seconds, floor-divided>
# healthy <= 0.5x the stale threshold, degraded <= 1.0x (same boundary
# health-check.sh/verify-r2.sh already alert past), stale beyond that.
classify_ratio() {
    local ratio_milli="$1"
    if [ "$ratio_milli" -le 500 ]; then
        echo healthy
    elif [ "$ratio_milli" -le 1000 ]; then
        echo degraded
    else
        echo stale
    fi
}

if [ ! -f "$TSV" ]; then
    echo "status-snapshot: agencies roster $TSV is missing — reporting unknown" >&2
    rt_state=unknown; rt_epoch=""
    static_state=unknown; static_epoch=""
    agencies_configured=0
else
    agencies_configured=0
    rt_missing_ever=0
    rt_worst_ratio=-1
    rt_worst_epoch=""
    static_configured=0
    static_missing_ever=0
    static_worst_ratio=-1
    static_worst_epoch=""

    while IFS= read -r row || [ -n "${row:-}" ]; do
        agency_row_is_data "$row" || continue
        split_agency_row "$row"
        agencies_configured=$((agencies_configured + 1))
        id="$agency_id"
        interval="$agency_interval"
        static="$agency_static_url"

        rt_dir="$BASE_DIR/data/$id/rt"
        newest_rt=$(ls -1d "$rt_dir"/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/TripUpdate_*.pb \
            2>/dev/null | sort | tail -1)
        if [ -z "$newest_rt" ]; then
            rt_missing_ever=1
        else
            rt_epoch=$(file_epoch "$newest_rt")
            case "$interval" in
                ''|*[!0-9]*|0) interval=1 ;;  # invalid config: don't crash, just treat as maximally strict
            esac
            if [ -n "$rt_epoch" ]; then
                age=$(( NOW - rt_epoch ))
                max=$(( interval * RT_STALE_FACTOR ))
                [ "$max" -gt 0 ] || max=1
                ratio=$(( age * 1000 / max ))
                if [ "$ratio" -gt "$rt_worst_ratio" ]; then
                    rt_worst_ratio="$ratio"
                    rt_worst_epoch="$rt_epoch"
                fi
            else
                rt_missing_ever=1
            fi
        fi

        [ -n "${static:-}" ] || continue
        static_configured=$((static_configured + 1))
        static_marker="$BASE_DIR/data/$id/static/.static-last-ok"
        if [ ! -f "$static_marker" ]; then
            static_missing_ever=1
            continue
        fi
        static_epoch=$(file_epoch "$static_marker")
        if [ -z "$static_epoch" ]; then
            static_missing_ever=1
            continue
        fi
        age=$(( NOW - static_epoch ))
        max=$(( STATIC_MAX_STALE_DAYS * 86400 ))
        [ "$max" -gt 0 ] || max=1
        ratio=$(( age * 1000 / max ))
        if [ "$ratio" -gt "$static_worst_ratio" ]; then
            static_worst_ratio="$ratio"
            static_worst_epoch="$static_epoch"
        fi
    done < "$TSV"

    if [ "$agencies_configured" -eq 0 ]; then
        rt_state=unknown; rt_epoch=""
        static_state=not_applicable; static_epoch=""
    else
        if [ "$rt_missing_ever" -eq 1 ]; then
            rt_state=unknown; rt_epoch=""
        else
            rt_state=$(classify_ratio "$rt_worst_ratio")
            rt_epoch="$rt_worst_epoch"
        fi
        if [ "$static_configured" -eq 0 ]; then
            static_state=not_applicable; static_epoch=""
        elif [ "$static_missing_ever" -eq 1 ]; then
            static_state=unknown; static_epoch=""
        else
            static_state=$(classify_ratio "$static_worst_ratio")
            static_epoch="$static_worst_epoch"
        fi
    fi
fi

# --- R2 sync (sync-r2.sh's own success marker) ---
if [ ! -f "$SYNC_OK_MARKER" ]; then
    sync_state=unknown; sync_epoch=""
else
    # sync-r2.sh writes the marker's CONTENT as the success timestamp itself
    # (not just relying on mtime), so read that first; fall back to mtime for
    # a marker written some other way (e.g. by hand during a migration).
    sync_content=$(cat "$SYNC_OK_MARKER" 2>/dev/null || true)
    sync_epoch=$(parse_iso_epoch "$sync_content")
    [ -n "$sync_epoch" ] || sync_epoch=$(file_epoch "$SYNC_OK_MARKER")
    if [ -z "$sync_epoch" ]; then
        sync_state=unknown; sync_epoch=""
    else
        age=$(( NOW - sync_epoch ))
        max=$(( SYNC_R2_MAX_STALE_DAYS * 86400 ))
        [ "$max" -gt 0 ] || max=1
        sync_state=$(classify_ratio $(( age * 1000 / max )))
    fi
fi

# --- R2 verify (verify-r2.sh's own result marker: "<iso> <ok|fail> <count>") ---
verify_result="unknown"
r2_object_total=""
if [ ! -f "$VERIFY_RESULT_MARKER" ]; then
    verify_state=unknown; verify_epoch=""
else
    read -r verify_ts verify_result verify_total < "$VERIFY_RESULT_MARKER" 2>/dev/null || true
    verify_epoch=$(parse_iso_epoch "${verify_ts:-}")
    if [ -z "$verify_epoch" ]; then
        verify_state=unknown; verify_epoch=""; verify_result="unknown"
    else
        case "${verify_total:-}" in
            ''|*[!0-9]*) ;;
            *) r2_object_total="$verify_total" ;;
        esac
        if [ "$verify_result" = "fail" ]; then
            verify_state=failed
        else
            age=$(( NOW - verify_epoch ))
            max=$(( VERIFY_R2_MAX_STALE_DAYS * 86400 ))
            [ "$max" -gt 0 ] || max=1
            verify_state=$(classify_ratio $(( age * 1000 / max )))
        fi
    fi
fi

# r2_state is the worse of sync/verify; ties keep sync's own epoch, matching
# the fixed rt > static > sync > verify priority order used below.
if [ "$(severity_rank "$verify_state")" -gt "$(severity_rank "$sync_state")" ]; then
    r2_state="$verify_state"; r2_epoch="$verify_epoch"
else
    r2_state="$sync_state"; r2_epoch="$sync_epoch"
fi

# --- combine rt/static/r2 into one document-level state ---
overall_state=healthy
overall_epoch=""
overall_rank=-1
for pair in "rt:$rt_state:$rt_epoch" "static:$static_state:$static_epoch" "r2:$r2_state:$r2_epoch"; do
    name="${pair%%:*}"
    rest="${pair#*:}"
    state="${rest%%:*}"
    epoch="${rest#*:}"
    [ "$state" = not_applicable ] && continue
    rank=$(severity_rank "$state")
    if [ "$rank" -gt "$overall_rank" ]; then
        overall_rank="$rank"
        overall_state="$state"
        overall_epoch="$epoch"
    fi
done
[ "$overall_rank" -ge 0 ] || { overall_state=unknown; overall_epoch=""; }

# `overall_epoch` is empty exactly when `overall_state` is `unknown`: every
# other reachable state (healthy/degraded/stale from a ratio classification,
# or `failed` from verify-r2.sh's own explicit "fail" result) is only ever
# assigned together with a real timestamp above.
if [ "$overall_state" = unknown ]; then
    last_success_json=null
    age_seconds_json=null
else
    last_success_json="\"$(epoch_to_iso "$overall_epoch")\""
    age_seconds_json=$(( NOW - overall_epoch ))
fi

observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# --- disk usage for $BASE_DIR's filesystem ---
disk_used_pct=null
disk_free_bytes=null
if df_line=$(df -Pk "$BASE_DIR" 2>/dev/null | awk 'NR==2 {print $4, $5}'); then
    avail_kb=$(printf '%s' "$df_line" | cut -d' ' -f1)
    pct=$(printf '%s' "$df_line" | cut -d' ' -f2)
    pct="${pct%\%}"
    case "$avail_kb" in ''|*[!0-9]*) ;; *) disk_free_bytes=$(( avail_kb * 1024 ));; esac
    case "$pct" in ''|*[!0-9]*) ;; *) disk_used_pct="$pct";; esac
fi

num_or_null() { [ -n "${1:-}" ] && [ "$1" != null ] && echo "$1" || echo null; }
age_or_null() { [ -n "${1:-}" ] && echo $(( NOW - $1 )) || echo null; }

rt_worst_age_seconds=$(age_or_null "$rt_epoch")
static_worst_age_seconds=$(age_or_null "$static_epoch")
sync_marker_age_seconds=$(age_or_null "$sync_epoch")
verify_age_seconds=$(age_or_null "$verify_epoch")
r2_object_total_json=$(num_or_null "$r2_object_total")
disk_used_pct_json=$(num_or_null "$disk_used_pct")
disk_free_bytes_json=$(num_or_null "$disk_free_bytes")

DETAILS=$(cat <<JSON
{"agencies_configured":$agencies_configured,"rt_state":"$rt_state","static_state":"$static_state","r2_state":"$r2_state","verify_result":"$verify_result","rt_worst_age_seconds":$rt_worst_age_seconds,"static_worst_age_seconds":$static_worst_age_seconds,"sync_marker_age_seconds":$sync_marker_age_seconds,"verify_age_seconds":$verify_age_seconds,"r2_object_total":$r2_object_total_json,"disk_used_pct":$disk_used_pct_json,"disk_free_bytes":$disk_free_bytes_json}
JSON
)

DOCUMENT=$(cat <<JSON
{"schema_version":1,"component":"oracle_crawler","state":"$overall_state","observed_at":"$observed_at","last_success_at":$last_success_json,"age_seconds":$age_seconds_json,"details":$DETAILS}
JSON
)

size=$(printf '%s' "$DOCUMENT" | wc -c | tr -d ' ')
if [ "$size" -gt 4096 ]; then
    echo "status-snapshot: BUG — built a ${size}-byte document, over the 4096-byte contract limit" >&2
    exit 70
fi

mkdir -p "$(dirname "$OUT_FILE")" || { echo "status-snapshot: cannot create $(dirname "$OUT_FILE")" >&2; exit 1; }
tmp_out=$(mktemp "$OUT_FILE.XXXXXX") || { echo "status-snapshot: mktemp failed" >&2; exit 1; }
printf '%s\n' "$DOCUMENT" > "$tmp_out" && mv -f "$tmp_out" "$OUT_FILE" || {
    echo "status-snapshot: failed to atomically write $OUT_FILE" >&2
    rm -f "$tmp_out"
    exit 1
}

printf '%s\n' "$DOCUMENT"
echo "status-snapshot: wrote $OUT_FILE (state=$overall_state)"
