#!/usr/bin/env bash
# storage-metrics.sh: builds one operations-status document (schema_version 1,
# component "r2") reporting local filesystem usage and R2 object count/bytes
# split by rt/ and static/, classified against configurable warning/critical
# thresholds. Always exits 0 on a well-formed document -- crossing a
# threshold, or R2 credentials not being configured, is a normal reported
# state, not a script failure.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

OUT="$COLLECTOR_BASE/.status/r2-storage-status.json"
SUCCESS_MARKER="$COLLECTOR_BASE/.storage-metrics.last-success"

export OBJECT_STORE_ENDPOINT="https://example.r2.cloudflarestorage.com"
export OBJECT_STORE_BUCKET="test-bucket"
export OBJECT_STORE_ACCESS_KEY_ID="AKIDTEST"
export OBJECT_STORE_SECRET_ACCESS_KEY="secrettest"

export AWS_LOG="$TEST_BASE/aws.log"

# The fake `aws` responds to `aws s3 ls --recursive --endpoint-url <url>
# s3://<bucket>/<prefix>` (matching spool-cleanup.sh's own shim convention in
# test_spool_cleanup.sh) by printing the fixture lines from `LS_<sanitized-
# prefix>`, one `<date> <time> <size> <key>` row per line, exactly as real
# `aws s3 ls --recursive` output looks -- so a test can seed however many
# objects it wants for a prefix in one shimmed call. An unset/empty fixture
# means "prefix lists as empty" (0 objects), not a shim error. `AWS_EXIT_
# <prefix>` forces a nonzero exit for that prefix, simulating a listing
# failure, and writes `AWS_ERR_<prefix>` (or a default message) to stderr so
# tests can assert the script captures and logs it.
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    url=""
    for a in "$@"; do
        case "$a" in
            s3://*) url="$a" ;;
        esac
    done
    prefix="${url#s3://*/}"
    key=$(printf '%s' "$prefix" | tr -c 'A-Za-z0-9' '_')
    exit_var="AWS_EXIT_${key}"
    if [ "${!exit_var:-0}" = "1" ]; then
        err_var="AWS_ERR_${key}"
        printf '%s\n' "${!err_var:-simulated aws s3 ls failure}" >&2
        exit 1
    fi
    var="LS_$key"
    if [ -n "${!var:-}" ]; then
        printf '%s\n' "${!var}"
    fi
    exit 0
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"

run_metrics() {
    : > "$AWS_LOG"
    set +e
    ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}

reset_env() {
    export LS_rt_=""
    export LS_static_=""
    unset AWS_EXIT_rt_ AWS_EXIT_static_ AWS_ERR_rt_ AWS_ERR_static_ 2>/dev/null || true
    rm -f "$SUCCESS_MARKER" "$OUT"
}

# obj_line <size> [<key>] -- one fixture row in real `aws s3 ls --recursive`
# format, for seeding LS_rt_/LS_static_ with a given object size.
obj_line() { printf '2024-01-01 00:00:00 %10d %s' "$1" "${2:-obj}"; }

have_python3=1
command -v python3 >/dev/null 2>&1 || have_python3=0
OPS_STATUS_PY="$(cd ../../.. && pwd)/scripts/ops_status.py"

# --- basic happy path ---------------------------------------------------

reset_env
export LS_rt_=$'2024-01-01 00:00:00        100 rt/a.json.gz\n2024-01-01 00:00:01        200 rt/b.json.gz'
export LS_static_=$'2024-01-01 00:00:00        100 static/a.js\n2024-01-01 00:00:01        300 static/b.js\n2024-01-01 00:00:02        500 static/c.js'
run_metrics
[ "$rc" -eq 0 ] || fail "a healthy run should exit 0: $(cat "$TEST_BASE/out.log")"
[ -f "$OUT" ] || fail "no status document was written"
grep -q '"component":"r2"' "$OUT" || fail "component field missing/wrong"
grep -q '"schema_version":1' "$OUT" || fail "schema_version field missing/wrong"
grep -q '"state":"healthy"' "$OUT" || fail "a fresh, low-usage run should report state=healthy: $(cat "$OUT")"
grep -q '"rt_object_count":2' "$OUT" || fail "rt_object_count missing/wrong: $(cat "$OUT")"
grep -q '"rt_bytes":300' "$OUT" || fail "rt_bytes missing/wrong: $(cat "$OUT")"
grep -q '"static_object_count":3' "$OUT" || fail "static_object_count missing/wrong: $(cat "$OUT")"
grep -q '"static_bytes":900' "$OUT" || fail "static_bytes missing/wrong: $(cat "$OUT")"
grep -q '"r2_bytes_total":1200' "$OUT" || fail "r2_bytes_total should be rt+static: $(cat "$OUT")"
grep -q '"r2_configured":true' "$OUT" || fail "r2_configured should be true when OBJECT_STORE_* is set: $(cat "$OUT")"
pass "a healthy, low-usage run reports state=healthy with correct rt/static counts and bytes"

[ -f "$SUCCESS_MARKER" ] || fail "a successful listing should write the success marker"
pass "a successful listing writes the success marker"

grep -q -- "--recursive" "$AWS_LOG" || fail "the listing should use aws s3 ls --recursive"
grep -q -- "--endpoint-url" "$AWS_LOG" || fail "the listing should pass --endpoint-url"
pass "the listing invokes aws s3 ls --recursive --endpoint-url"

# --- multi-object summation ------------------------------------------------

# A single `aws s3 ls --recursive` listing can return any number of objects;
# confirm three fixture lines with different sizes are correctly summed into
# one object_count/bytes total in one shimmed call (no NextToken/page concept
# remains -- `aws s3 ls --recursive` paginates internally).
reset_env
export LS_rt_=$'2024-01-01 00:00:00         10 rt/1.json.gz\n2024-01-01 00:00:01         20 rt/2.json.gz\n2024-01-01 00:00:02         30 rt/3.json.gz'
export LS_static_=""
run_metrics
[ "$rc" -eq 0 ] || fail "a multi-object rt/ listing should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"rt_object_count":3' "$OUT" || fail "three fixture lines should sum to 3 objects: $(cat "$OUT")"
grep -q '"rt_bytes":60' "$OUT" || fail "three fixture lines (10+20+30) should sum to 60 bytes: $(cat "$OUT")"
grep -q '"static_object_count":0' "$OUT" || fail "an empty static/ listing should report 0 objects: $(cat "$OUT")"
pass "multiple objects returned by a single aws s3 ls --recursive call are correctly summed"

# --- missing credentials --------------------------------------------------

reset_env
run_metrics_no_creds() {
    : > "$AWS_LOG"
    set +e
    OBJECT_STORE_ENDPOINT= OBJECT_STORE_BUCKET= OBJECT_STORE_ACCESS_KEY_ID= OBJECT_STORE_SECRET_ACCESS_KEY= \
        ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}
run_metrics_no_creds
[ "$rc" -eq 0 ] || fail "missing OBJECT_STORE_* should still exit 0 (a supported configuration): $(cat "$TEST_BASE/out.log")"
[ -s "$AWS_LOG" ] && fail "aws should never be invoked when OBJECT_STORE_* is unset"
grep -q '"r2_configured":false' "$OUT" || fail "r2_configured should be false when credentials are missing: $(cat "$OUT")"
grep -q '"r2_state":"not_applicable"' "$OUT" || fail "r2_state should be not_applicable when credentials are missing: $(cat "$OUT")"
grep -q '"rt_object_count":null' "$OUT" || fail "rt_object_count should be null when R2 was never listed: $(cat "$OUT")"
grep -q '"disk_used_pct":null' "$OUT" && fail "disk usage should still be reported even without R2 credentials: $(cat "$OUT")"
pass "missing OBJECT_STORE_* credentials reports r2_state=not_applicable without ever calling aws, while disk usage is still reported"

# not_applicable must not drag a healthy disk down to unknown/degraded.
grep -q '"state":"healthy"' "$OUT" || fail "r2 being not_applicable must not drag the combined state down: $(cat "$OUT")"
pass "r2_state=not_applicable does not drag the combined document state down from healthy"

# --- partial R2 listing failures ------------------------------------------

# rt/ lists fine; static/ errors outright.
reset_env
export LS_rt_=$'2024-01-01 00:00:00        100 rt/a.json.gz\n2024-01-01 00:00:01        200 rt/b.json.gz'
export AWS_EXIT_static_=1
export AWS_ERR_static_="An error occurred (AccessDenied) when calling the ListObjectsV2 operation"
run_metrics
[ "$rc" -eq 0 ] || fail "a partial listing failure should still exit 0 (reported, not a script crash): $(cat "$TEST_BASE/out.log")"
grep -q '"r2_state":"failed"' "$OUT" || fail "a partial listing failure should report r2_state=failed: $(cat "$OUT")"
grep -q '"r2_listing_result":"fail"' "$OUT" || fail "r2_listing_result should be fail: $(cat "$OUT")"
grep -q '"rt_object_count":2' "$OUT" || fail "the prefix that DID list successfully should still report its real numbers: $(cat "$OUT")"
grep -q '"rt_bytes":300' "$OUT" || fail "the prefix that DID list successfully should still report its real bytes: $(cat "$OUT")"
grep -q '"static_object_count":null' "$OUT" || fail "the prefix that failed to list should report null, not a stale/guessed number: $(cat "$OUT")"
grep -q '"r2_bytes_total":null' "$OUT" || fail "r2_bytes_total should be null when the listing is incomplete: $(cat "$OUT")"
grep -q '"state":"failed"' "$OUT" || fail "a failed r2 listing should push the combined document state to failed: $(cat "$OUT")"
pass "one prefix failing to list while the other succeeds reports r2_state=failed with the failed prefix's numbers null, not stale"

grep -q "AccessDenied" "$TEST_BASE/out.log" || \
    fail "the captured aws s3 ls stderr should appear in storage-metrics.sh's own logged output, not be discarded: $(cat "$TEST_BASE/out.log")"
pass "a listing failure's captured aws stderr is included in the script's logged failure message"

# A listing failure must not touch the success marker from an earlier run,
# and must report that earlier success's own timestamp, not this run's.
reset_env
export LS_rt_="$(obj_line 10 rt/a.json.gz)"
export LS_static_="$(obj_line 10 static/a.js)"
run_metrics
[ "$rc" -eq 0 ] || fail "seeding a prior success should exit 0: $(cat "$TEST_BASE/out.log")"
prior_marker=$(cat "$SUCCESS_MARKER")
export AWS_EXIT_rt_=1
run_metrics
[ "$rc" -eq 0 ] || fail "a listing failure after a prior success should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q "\"last_success_at\":\"$prior_marker\"" "$OUT" || \
    fail "a listing failure should report the prior run's real success timestamp, not this run's own: $(cat "$OUT")"
[ "$(cat "$SUCCESS_MARKER")" = "$prior_marker" ] || fail "a failing run must not overwrite the success marker"
pass "a listing failure reports the last genuine success's timestamp and leaves the success marker untouched"

# A listing failure with NO prior success on record reports last_success_at:null.
reset_env
export AWS_EXIT_rt_=1
export LS_static_="$(obj_line 10 static/a.js)"
run_metrics
[ "$rc" -eq 0 ] || fail "a first-ever listing failure should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"last_success_at":null' "$OUT" || fail "a listing failure with no prior success must report last_success_at:null: $(cat "$OUT")"
grep -q '"age_seconds":null' "$OUT" || fail "a listing failure with no prior success must report age_seconds:null: $(cat "$OUT")"
[ -f "$SUCCESS_MARKER" ] && fail "a failing run must not create a success marker out of thin air"
pass "a first-ever listing failure with no prior success reports last_success_at:null"

# --- threshold boundaries: R2 bytes ---------------------------------------

# Exactly at the warning threshold: already degraded (inclusive boundary).
reset_env
export LS_rt_="$(obj_line 1000 rt/a.json.gz)"
export LS_static_=""
set +e
R2_BYTES_WARN_THRESHOLD=1000 R2_BYTES_CRIT_THRESHOLD=2000 ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "hitting the warn threshold exactly should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"r2_state":"degraded"' "$OUT" || fail "r2_bytes_total exactly at the warn threshold should be degraded: $(cat "$OUT")"
pass "r2_bytes_total exactly at the warn threshold is degraded (inclusive boundary)"

# One byte below the warning threshold: still healthy.
reset_env
export LS_rt_="$(obj_line 999 rt/a.json.gz)"
export LS_static_=""
set +e
R2_BYTES_WARN_THRESHOLD=1000 R2_BYTES_CRIT_THRESHOLD=2000 ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
grep -q '"r2_state":"healthy"' "$OUT" || fail "r2_bytes_total one byte below warn should still be healthy: $(cat "$OUT")"
pass "r2_bytes_total one byte below the warn threshold is healthy"

# Exactly at the critical threshold: already failed (inclusive boundary).
reset_env
export LS_rt_="$(obj_line 2000 rt/a.json.gz)"
export LS_static_=""
set +e
R2_BYTES_WARN_THRESHOLD=1000 R2_BYTES_CRIT_THRESHOLD=2000 ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
grep -q '"r2_state":"failed"' "$OUT" || fail "r2_bytes_total exactly at the critical threshold should be failed: $(cat "$OUT")"
pass "r2_bytes_total exactly at the critical threshold is failed (inclusive boundary)"

# One byte below critical: degraded, not failed.
reset_env
export LS_rt_="$(obj_line 1999 rt/a.json.gz)"
export LS_static_=""
set +e
R2_BYTES_WARN_THRESHOLD=1000 R2_BYTES_CRIT_THRESHOLD=2000 ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
grep -q '"r2_state":"degraded"' "$OUT" || fail "r2_bytes_total one byte below critical should be degraded, not failed: $(cat "$OUT")"
pass "r2_bytes_total one byte below the critical threshold is degraded, not failed"

# --- threshold boundaries: disk usage --------------------------------------

reset_env
cat > "$SHIM_DIR/df" <<'SHIM'
#!/usr/bin/env bash
printf 'Filesystem     1024-blocks      Used Available Capacity Mounted on\n'
printf 'tmpfs             1000000    800000    200000       80%% /test\n'
SHIM
chmod +x "$SHIM_DIR/df"
run_metrics
rm -f "$SHIM_DIR/df"
[ "$rc" -eq 0 ] || fail "disk usage exactly at the warn threshold should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"disk_state":"degraded"' "$OUT" || fail "disk_used_pct exactly at DISK_WARN_PCT (80) should be degraded: $(cat "$OUT")"
grep -q '"state":"degraded"' "$OUT" || fail "a degraded disk should push the combined document state to degraded: $(cat "$OUT")"
pass "disk_used_pct exactly at the warn threshold (80%) is degraded"

reset_env
cat > "$SHIM_DIR/df" <<'SHIM'
#!/usr/bin/env bash
printf 'Filesystem     1024-blocks      Used Available Capacity Mounted on\n'
printf 'tmpfs             1000000    900000    100000       90%% /test\n'
SHIM
chmod +x "$SHIM_DIR/df"
run_metrics
rm -f "$SHIM_DIR/df"
grep -q '"disk_state":"failed"' "$OUT" || fail "disk_used_pct exactly at DISK_CRIT_PCT (90) should be failed: $(cat "$OUT")"
grep -q '"state":"failed"' "$OUT" || fail "a failed disk should push the combined document state to failed: $(cat "$OUT")"
pass "disk_used_pct exactly at the critical threshold (90%) is failed"

# --- config validation -------------------------------------------------

reset_env
set +e
DISK_WARN_PCT=90 DISK_CRIT_PCT=80 ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "DISK_CRIT_PCT < DISK_WARN_PCT should be rejected with exit 64, got $rc"
[ -f "$OUT" ] && fail "a rejected threshold config must not still write a status document"
pass "DISK_CRIT_PCT less than DISK_WARN_PCT is rejected with exit 64 and writes nothing"

reset_env
set +e
R2_BYTES_WARN_THRESHOLD=2000 R2_BYTES_CRIT_THRESHOLD=1000 ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "R2_BYTES_CRIT_THRESHOLD < R2_BYTES_WARN_THRESHOLD should be rejected with exit 64, got $rc"
pass "R2_BYTES_CRIT_THRESHOLD less than R2_BYTES_WARN_THRESHOLD is rejected with exit 64"

reset_env
set +e
DISK_WARN_PCT=soon ../bin/storage-metrics.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "a non-numeric threshold should be rejected with exit 64, got $rc"
pass "a non-numeric threshold is rejected with exit 64"

# --- atomicity / idempotency -----------------------------------------------

reset_env
export LS_rt_="$(obj_line 10 rt/a.json.gz)"
export LS_static_="$(obj_line 10 static/a.js)"
run_metrics
leftover=$(find "$COLLECTOR_BASE/.status" -name 'r2-storage-status.json.??????' 2>/dev/null)
[ -z "$leftover" ] || fail "a temp file was left behind after an atomic write: $leftover"
pass "the status document is written atomically with no leftover temp file"

run_metrics
first_rc=$rc
run_metrics
[ "$rc" -eq 0 ] && [ "$first_rc" -eq 0 ] || fail "re-running storage-metrics.sh should be idempotent"
pass "re-running storage-metrics.sh is idempotent"

# --- contract validation -----------------------------------------------

if [ "$have_python3" -eq 1 ]; then
    reset_env
    export LS_rt_=$'2024-01-01 00:00:00        100 rt/a.json.gz\n2024-01-01 00:00:01        200 rt/b.json.gz'
    export LS_static_=$'2024-01-01 00:00:00        100 static/a.js\n2024-01-01 00:00:01        300 static/b.js\n2024-01-01 00:00:02        500 static/c.js'
    run_metrics
    python3 -c "import json; json.load(open('$OUT'))" || fail "the written document is not valid JSON"
    python3 "$OPS_STATUS_PY" --validate "$OUT" || \
        fail "a healthy document must satisfy scripts/ops_status.py's own contract validator: $(cat "$OUT")"
    pass "a healthy document satisfies scripts/ops_status.py's contract validator"

    reset_env
    export AWS_EXIT_rt_=1
    export LS_static_="$(obj_line 10 static/a.js)"
    run_metrics
    python3 "$OPS_STATUS_PY" --validate "$OUT" || \
        fail "a failed-with-no-prior-success document must satisfy scripts/ops_status.py's own contract validator: $(cat "$OUT")"
    pass "a failed-with-no-prior-success document satisfies scripts/ops_status.py's contract validator"

    reset_env
    run_metrics_no_creds
    python3 "$OPS_STATUS_PY" --validate "$OUT" || \
        fail "a not_applicable-r2 document must satisfy scripts/ops_status.py's own contract validator: $(cat "$OUT")"
    pass "a document with r2_state=not_applicable satisfies scripts/ops_status.py's contract validator"
fi
