#!/usr/bin/env bash
# Build one operations-status document (component "r2", see scripts/ops_status.py
# in the main repo checkout for the contract) reporting this VM's own
# filesystem usage and Cloudflare R2's object count/bytes split by the rt/ and
# static/ prefixes, and write it atomically to disk.
#
# Like status-snapshot.sh, this is a pure report, never a check: crossing a
# warning/critical threshold is a normal, valid THING TO REPORT (state=degraded/
# failed in the document itself), not a reason for this script to fail -- only
# a genuinely malformed run (bad threshold config, can't write the output
# file) exits nonzero. Unlike status-snapshot.sh, this DOES need R2
# credentials of its own: nothing else in bin/ already computes a whole-bucket
# object count/byte total, so this is the one script that lists R2 for that
# purpose (sync-r2.sh/verify-r2.sh/reconcile-r2.sh only ever look at one
# agency's or one object's own prefix, never the aggregate).
#
# Disk usage is always live (a single `df` call), so its own state's
# `last_success_at` is always "now" whenever `df` itself succeeds -- there is
# no separate freshness concept for a synchronous read. Missing OBJECT_STORE_*
# credentials is a supported configuration (matches publish-status.sh's own
# ORACLE_STATUS_GH_TOKEN precedent): r2_state reports "not_applicable" rather
# than dragging the combined document down to unknown/failed, since a VM
# that has not yet been given R2 credentials (or one where this check is
# intentionally disabled) is not thereby "broken".
#
# Once credentials ARE present, a listing failure (partial -- one of rt/static
# failed while the other succeeded -- or total) is reported as r2_state=failed
# regardless of any byte-threshold math, since a listing this script could not
# complete cannot be trusted to say "usage is fine". Its `last_success_at` for
# that case comes from SUCCESS_MARKER (updated only when both prefixes list
# successfully), never from the failing run's own timestamp -- mirroring
# verify-r2.sh's SUCCESS_MARKER/RESULT_MARKER split for exactly the same
# reason. Whichever prefix DID list successfully still reports its own real
# count/bytes in `details`; the failed one reports null rather than a stale
# number from a previous run.
#
# Each prefix is listed via `aws s3api list-objects-v2 --no-paginate`,
# followed page by page with our own `--starting-token` loop (rather than
# letting the CLI's own default auto-pagination hide every page but the
# first behind a single call) -- explicit pagination is what lets one bad
# page fail the listing without silently under-reporting a partial total as
# though it were complete.
set -uo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
AWS="${AWS_CLI:-aws}"
OUT_FILE="${STORAGE_METRICS_FILE:-$BASE_DIR/.status/r2-storage-status.json}"
SUCCESS_MARKER="${STORAGE_METRICS_SUCCESS_MARKER:-$BASE_DIR/.storage-metrics.last-success}"

# Percent-of-filesystem-used thresholds.
DISK_WARN_PCT="${DISK_WARN_PCT-80}"
DISK_CRIT_PCT="${DISK_CRIT_PCT-90}"
# Whole-bucket byte-total thresholds (rt + static combined). Defaults are a
# starting point for a small collector, not a measurement of any real
# deployment's actual usage -- override in /etc/environment once real volume
# is known.
R2_BYTES_WARN_THRESHOLD="${R2_BYTES_WARN_THRESHOLD-53687091200}"   # 50 GiB
R2_BYTES_CRIT_THRESHOLD="${R2_BYTES_CRIT_THRESHOLD-107374182400}"  # 100 GiB
# Hard cap on pages followed per prefix -- a defensive bound against a
# misbehaving/faked NextToken looping forever, not a real-world limit (at
# 1000 objects/page this is 10M objects per prefix).
STORAGE_METRICS_MAX_PAGES="${STORAGE_METRICS_MAX_PAGES-10000}"

for var in DISK_WARN_PCT DISK_CRIT_PCT R2_BYTES_WARN_THRESHOLD R2_BYTES_CRIT_THRESHOLD STORAGE_METRICS_MAX_PAGES; do
    value="${!var}"
    case "$value" in
        ''|*[!0-9]*)
            echo "storage-metrics: $var must be a non-negative integer, got '$value'" >&2
            exit 64
            ;;
    esac
    # Leading-zero normalization: a bare arithmetic context below would
    # otherwise read e.g. "080" as invalid octal and abort.
    printf -v "$var" '%d' "$((10#$value))"
done

if [ "$DISK_CRIT_PCT" -lt "$DISK_WARN_PCT" ]; then
    echo "storage-metrics: DISK_CRIT_PCT ($DISK_CRIT_PCT) must be >= DISK_WARN_PCT ($DISK_WARN_PCT)" >&2
    exit 64
fi
if [ "$R2_BYTES_CRIT_THRESHOLD" -lt "$R2_BYTES_WARN_THRESHOLD" ]; then
    echo "storage-metrics: R2_BYTES_CRIT_THRESHOLD ($R2_BYTES_CRIT_THRESHOLD) must be >= R2_BYTES_WARN_THRESHOLD ($R2_BYTES_WARN_THRESHOLD)" >&2
    exit 64
fi

NOW=$(date -u +%s)
observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

epoch_to_iso() {
    date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ
}

# classify_threshold <value> <warn> <crit> -> healthy | degraded | failed.
# Inclusive at each boundary: exactly warn is already degraded, exactly crit
# is already failed.
classify_threshold() {
    local value="$1" warn="$2" crit="$3"
    if [ "$value" -ge "$crit" ]; then
        echo failed
    elif [ "$value" -ge "$warn" ]; then
        echo degraded
    else
        echo healthy
    fi
}

# severity_rank <state> -> integer; higher is worse. -1 for not_applicable, so
# callers must exclude it before comparing rather than let it lose "by accident".
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

# --- disk usage for $BASE_DIR's filesystem ---
disk_used_pct=""
disk_free_bytes=""
disk_state=unknown
disk_epoch=""
if df_line=$(df -Pk "$BASE_DIR" 2>/dev/null | awk 'NR==2 {print $4, $5}'); then
    avail_kb=$(printf '%s' "$df_line" | cut -d' ' -f1)
    pct=$(printf '%s' "$df_line" | cut -d' ' -f2)
    pct="${pct%\%}"
    case "$avail_kb" in ''|*[!0-9]*) ;; *) disk_free_bytes=$(( 10#$avail_kb * 1024 ));; esac
    case "$pct" in
        ''|*[!0-9]*) ;;
        *)
            disk_used_pct=$(( 10#$pct ))
            disk_state=$(classify_threshold "$disk_used_pct" "$DISK_WARN_PCT" "$DISK_CRIT_PCT")
            disk_epoch="$NOW"
            ;;
    esac
fi

# --- R2 object count/bytes, split by rt/ and static/ ---
r2_configured=false
r2_state=not_applicable
r2_listing_result=unknown
rt_object_count=""
rt_bytes=""
static_object_count=""
static_bytes=""
r2_bytes_total=""
r2_epoch=""

if [ -n "${OBJECT_STORE_ENDPOINT:-}" ] && [ -n "${OBJECT_STORE_BUCKET:-}" ] \
    && [ -n "${OBJECT_STORE_ACCESS_KEY_ID:-}" ] && [ -n "${OBJECT_STORE_SECRET_ACCESS_KEY:-}" ]; then
    r2_configured=true
    export AWS_ACCESS_KEY_ID="$OBJECT_STORE_ACCESS_KEY_ID"
    export AWS_SECRET_ACCESS_KEY="$OBJECT_STORE_SECRET_ACCESS_KEY"

    # r2_list_prefix <prefix> -- prints "<count>\t<bytes>" and returns 0 on a
    # complete listing (every page followed to its end); returns 1 the moment
    # any single page's `aws` call fails, WITHOUT printing a partial total --
    # a partial count silently reported as though it were the true total
    # would understate real R2 usage exactly when something has already gone
    # wrong.
    r2_list_prefix() {
        local prefix="$1" token="" pages=0 count=0 bytes=0
        local line rc next page_count page_bytes
        while :; do
            pages=$((pages + 1))
            if [ "$pages" -gt "$STORAGE_METRICS_MAX_PAGES" ]; then
                echo "storage-metrics: prefix '$prefix' exceeded $STORAGE_METRICS_MAX_PAGES pages -- treating as a failed listing rather than looping forever" >&2
                return 1
            fi
            if [ -n "$token" ]; then
                line=$("$AWS" s3api list-objects-v2 --no-paginate \
                    --bucket "$OBJECT_STORE_BUCKET" --prefix "$prefix" \
                    --endpoint-url "$OBJECT_STORE_ENDPOINT" --starting-token "$token" \
                    --query '[NextToken, length(Contents[] || `[]`), sum(Contents[].Size || `[]`)]' \
                    --output text 2>/dev/null)
                rc=$?
            else
                line=$("$AWS" s3api list-objects-v2 --no-paginate \
                    --bucket "$OBJECT_STORE_BUCKET" --prefix "$prefix" \
                    --endpoint-url "$OBJECT_STORE_ENDPOINT" \
                    --query '[NextToken, length(Contents[] || `[]`), sum(Contents[].Size || `[]`)]' \
                    --output text 2>/dev/null)
                rc=$?
            fi
            [ "$rc" -eq 0 ] || return 1

            IFS=$'\t' read -r next page_count page_bytes <<< "$line"
            case "$page_count" in
                None|'') page_count=0 ;;
                *[!0-9]*) return 1 ;;
            esac
            case "$page_bytes" in
                None|'') page_bytes=0 ;;
                *[!0-9]*) return 1 ;;
            esac
            count=$(( count + 10#$page_count ))
            bytes=$(( bytes + 10#$page_bytes ))

            if [ "$next" = "None" ] || [ -z "$next" ]; then
                break
            fi
            token="$next"
        done
        printf '%s\t%s\n' "$count" "$bytes"
    }

    rt_result=$(r2_list_prefix "rt/")
    rt_ok=$?
    static_result=$(r2_list_prefix "static/")
    static_ok=$?

    if [ "$rt_ok" -eq 0 ]; then
        IFS=$'\t' read -r rt_object_count rt_bytes <<< "$rt_result"
    fi
    if [ "$static_ok" -eq 0 ]; then
        IFS=$'\t' read -r static_object_count static_bytes <<< "$static_result"
    fi

    if [ "$rt_ok" -eq 0 ] && [ "$static_ok" -eq 0 ]; then
        r2_listing_result=ok
        r2_bytes_total=$(( rt_bytes + static_bytes ))
        r2_state=$(classify_threshold "$r2_bytes_total" "$R2_BYTES_WARN_THRESHOLD" "$R2_BYTES_CRIT_THRESHOLD")
        r2_epoch="$NOW"
        tmp_marker=$(mktemp "$SUCCESS_MARKER.XXXXXX" 2>/dev/null) && {
            printf '%s\n' "$observed_at" > "$tmp_marker" && mv -f "$tmp_marker" "$SUCCESS_MARKER" \
                || rm -f "$tmp_marker"
        }
    else
        r2_listing_result=fail
        r2_state=failed
        [ "$rt_ok" -eq 0 ] || echo "storage-metrics: listing rt/ failed" >&2
        [ "$static_ok" -eq 0 ] || echo "storage-metrics: listing static/ failed" >&2
        if [ -f "$SUCCESS_MARKER" ]; then
            marker_ts=$(cat "$SUCCESS_MARKER" 2>/dev/null || true)
            r2_epoch=$(date -u -d "$marker_ts" +%s 2>/dev/null \
                || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$marker_ts" +%s 2>/dev/null || true)
        fi
    fi
fi

# --- combine disk/r2 into one document-level state (worst-of, not_applicable excluded) ---
overall_state=healthy
overall_epoch=""
overall_rank=-1
for pair in "disk:$disk_state:$disk_epoch" "r2:$r2_state:$r2_epoch"; do
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

if [ -z "$overall_epoch" ]; then
    last_success_json=null
    age_seconds_json=null
else
    last_success_json="\"$(epoch_to_iso "$overall_epoch")\""
    age_seconds_json=$(( NOW - overall_epoch ))
    [ "$age_seconds_json" -ge 0 ] || age_seconds_json=0
fi

num_or_null() { [ -n "${1:-}" ] && echo "$1" || echo null; }

DETAILS=$(cat <<JSON
{"disk_used_pct":$(num_or_null "$disk_used_pct"),"disk_free_bytes":$(num_or_null "$disk_free_bytes"),"disk_state":"$disk_state","r2_configured":$r2_configured,"r2_state":"$r2_state","r2_listing_result":"$r2_listing_result","rt_object_count":$(num_or_null "$rt_object_count"),"rt_bytes":$(num_or_null "$rt_bytes"),"static_object_count":$(num_or_null "$static_object_count"),"static_bytes":$(num_or_null "$static_bytes"),"r2_bytes_total":$(num_or_null "$r2_bytes_total")}
JSON
)

DOCUMENT=$(cat <<JSON
{"schema_version":1,"component":"r2","state":"$overall_state","observed_at":"$observed_at","last_success_at":$last_success_json,"age_seconds":$age_seconds_json,"details":$DETAILS}
JSON
)

size=$(printf '%s' "$DOCUMENT" | wc -c | tr -d ' ')
if [ "$size" -gt 4096 ]; then
    echo "storage-metrics: BUG — built a ${size}-byte document, over the 4096-byte contract limit" >&2
    exit 70
fi

mkdir -p "$(dirname "$OUT_FILE")" || { echo "storage-metrics: cannot create $(dirname "$OUT_FILE")" >&2; exit 1; }
tmp_out=$(mktemp "$OUT_FILE.XXXXXX") || { echo "storage-metrics: mktemp failed" >&2; exit 1; }
printf '%s\n' "$DOCUMENT" > "$tmp_out" && mv -f "$tmp_out" "$OUT_FILE" || {
    echo "storage-metrics: failed to atomically write $OUT_FILE" >&2
    rm -f "$tmp_out"
    exit 1
}

printf '%s\n' "$DOCUMENT"
echo "storage-metrics: wrote $OUT_FILE (state=$overall_state)"
