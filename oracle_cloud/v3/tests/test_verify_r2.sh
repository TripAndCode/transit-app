#!/usr/bin/env bash
# verify-r2.sh: independently lists each configured agency's R2 objects and
# checks freshness + byte-size integrity against local disk, rather than
# trusting sync-r2.sh's own exit status.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

export OBJECT_STORE_ENDPOINT="https://example.r2.cloudflarestorage.com"
export OBJECT_STORE_BUCKET="test-bucket"
export OBJECT_STORE_ACCESS_KEY_ID="AKIDTEST"
export OBJECT_STORE_SECRET_ACCESS_KEY="secrettest"

now_ts() { date -u +%Y-%m-%d\ %H:%M:%S; }
days_ago_ts() {
    date -u -v-"$1"d +%Y-%m-%d\ %H:%M:%S 2>/dev/null || date -u -d "$1 days ago" +%Y-%m-%d\ %H:%M:%S
}

# LS_FIXTURE holds the canned `aws s3 ls --recursive` output to return, keyed
# by the prefix argument, one "prefix=lines..." block set via env before each
# call. Simpler: the shim reads $LS_RT_<id> / $LS_STATIC_<id> env vars named
# after the sanitized prefix it was asked to list. A file of that name under
# LS_FIXTURE_DIR takes precedence, so a background process can change what
# R2 "holds" while verify-r2.sh is running.
export AWS_LOG="$TEST_BASE/aws.log"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    # args: s3 ls s3://bucket/PREFIX --recursive --endpoint-url URL
    url="$3"
    prefix="${url#s3://*/}"
    var="LS_$(printf '%s' "$prefix" | tr -c 'A-Za-z0-9' '_')"
    if [ -n "${LS_FIXTURE_DIR:-}" ] && [ -f "$LS_FIXTURE_DIR/$var" ]; then
        cat "$LS_FIXTURE_DIR/$var"
    else
        printf '%s' "${!var:-}"
    fi
    exit 0
fi
if [ "$1" = s3 ] && [ "$2" = rm ]; then
    exit "${AWS_RM_EXIT:-0}"
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"

printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
printf '1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"
printf '8\thiroden\t60\thttp://feed.test/tu8.pb\thttp://feed.test/s8.zip\thttp://ping.test/8\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"

mkdir -p "$COLLECTOR_BASE/data/1/rt" "$COLLECTOR_BASE/data/8/rt" "$COLLECTOR_BASE/data/8/static"
printf 'RTDATA-1' > "$COLLECTOR_BASE/data/1/rt/20260909.tar.gz"
printf 'RTDATA-8' > "$COLLECTOR_BASE/data/8/rt/20260909.tar.gz"
head -c 2048 /dev/zero > "$COLLECTOR_BASE/data/8/static/gtfs_static_20260909.zip"
ln -sfn gtfs_static_20260909.zip "$COLLECTOR_BASE/data/8/static/latest.zip"
rt1_size=$(wc -c < "$COLLECTOR_BASE/data/1/rt/20260909.tar.gz" | tr -d ' ')
rt8_size=$(wc -c < "$COLLECTOR_BASE/data/8/rt/20260909.tar.gz" | tr -d ' ')
static8_size=$(wc -c < "$COLLECTOR_BASE/data/8/static/gtfs_static_20260909.zip" | tr -d ' ')

fresh=$(now_ts)
seed_healthy() {
    export LS_rt_1_="$fresh $rt1_size rt/1/20260909.tar.gz"$'\n'
    export LS_rt_8_="$fresh $rt8_size rt/8/20260909.tar.gz"$'\n'
    export LS_static_8_="$fresh $static8_size static/8/gtfs_static_20260909.zip"$'\n'
}

run_verify() {
    : > "$AWS_LOG"
    set +e
    ../bin/verify-r2.sh > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}

# A fully fresh, intact mirror passes cleanly.
seed_healthy
run_verify
[ "$rc" -eq 0 ] || fail "a fresh, intact R2 mirror should exit 0: $(cat "$TEST_BASE/out.log")"
grep -q "2 agencies checked" "$TEST_BASE/out.log" || fail "success summary missing agency count"
pass "a fresh, intact R2 mirror for every agency exits 0"

# A successful run records a result marker: "<iso> ok <total_object_count>",
# read by status-snapshot.sh for the oracle_crawler heartbeat -- 3 objects
# here (rt/1, rt/8, static/8, one line each from seed_healthy's LS_ fixtures).
marker="$COLLECTOR_BASE/.verify-r2.last-result"
[ -f "$marker" ] || fail "verify-r2.sh did not write a result marker on success"
read -r marker_ts marker_result marker_total < "$marker"
[ "$marker_result" = "ok" ] || fail "a successful run should record result=ok, got '$marker_result'"
[ "$marker_total" = "3" ] || fail "expected a total of 3 objects, got '$marker_total'"
{ date -u -d "$marker_ts" +%s >/dev/null 2>&1 || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$marker_ts" +%s >/dev/null 2>&1; } \
    || fail "the marker's timestamp is not a valid ISO 8601 UTC timestamp: $marker_ts"
pass "a successful run records an ok result marker with the total object count"

# A successful run also records a separate success-only marker -- the one
# status-snapshot.sh reads for this subsystem's genuine last_success_at,
# distinct from the result marker written on every completed run either way.
success_marker="$COLLECTOR_BASE/.verify-r2.last-success"
[ -f "$success_marker" ] || fail "verify-r2.sh did not write a success marker on success"
success_ts=$(cat "$success_marker")
[ "$success_ts" = "$marker_ts" ] || fail "the success marker's timestamp should match the result marker's on a successful run, got '$success_ts' vs '$marker_ts'"
pass "a successful run records a success-only marker matching the result marker's timestamp"

# Missing required env fails closed before calling aws.
: > "$AWS_LOG"
if OBJECT_STORE_ENDPOINT= OBJECT_STORE_BUCKET= OBJECT_STORE_ACCESS_KEY_ID= OBJECT_STORE_SECRET_ACCESS_KEY= \
    ../bin/verify-r2.sh 2>/dev/null; then
    fail "verify-r2.sh should fail when OBJECT_STORE_* is unset"
fi
[ -s "$AWS_LOG" ] && fail "aws was invoked despite missing required env"
pass "missing OBJECT_STORE_* fails closed before calling aws"

# No RT objects at all in R2 for a configured agency.
seed_healthy
unset LS_rt_1_
run_verify
[ "$rc" -eq 1 ] || fail "no RT objects in R2 should exit 1, got $rc"
grep -q "no RT objects in R2 under rt/1/" "$TEST_BASE/out.log" || fail "missing-RT-objects reason absent"
pass "an agency with no RT objects in R2 fails"

# A failing run still records a result marker -- "fail", not silently absent,
# so a heartbeat reading it can't mistake "never checked" for "just checked
# and failed".
read -r marker_ts marker_result marker_total < "$COLLECTOR_BASE/.verify-r2.last-result"
[ "$marker_result" = "fail" ] || fail "a failing run should record result=fail, got '$marker_result'"
pass "a failing run records a fail result marker rather than leaving none at all"

# A failing run must NOT touch the success marker -- a genuine prior success
# stays on record exactly as it was, rather than being silently lost or
# overwritten with the failed run's own timestamp.
[ "$(cat "$success_marker")" = "$success_ts" ] \
    || fail "a failing run must not modify the success marker left by an earlier success"
pass "a failing run leaves a prior success marker untouched"

# A run that fails with no prior success on record at all must not create a
# success marker out of thin air.
rm -f "$success_marker"
run_verify
[ "$rc" -eq 1 ] || fail "expected the still-broken fixture (no RT objects for agency 1) to keep failing, got rc=$rc"
[ ! -f "$success_marker" ] || fail "a failing run must not create a success marker when there has never been a real success"
pass "a failing run with no prior success on record does not create a success marker"

# Newest RT object in R2 is stale (rotate-day/sync-r2 stopped mirroring it).
seed_healthy
stale=$(days_ago_ts 10)
export LS_rt_1_="$stale $rt1_size rt/1/20260830.tar.gz"$'\n'
run_verify
[ "$rc" -eq 1 ] || fail "stale RT object in R2 should exit 1, got $rc"
grep -q "newest R2 RT object rt/1/20260830.tar.gz is" "$TEST_BASE/out.log" || fail "stale-RT reason absent"
pass "a stale newest RT object in R2 fails"

# The R2 object exists but is a truncated/corrupted transfer (size mismatch).
seed_healthy
export LS_rt_8_="$fresh 3 rt/8/20260909.tar.gz"$'\n'
run_verify
[ "$rc" -eq 1 ] || fail "an RT size mismatch should exit 1, got $rc"
grep -q "integrity check failed" "$TEST_BASE/out.log" || fail "RT integrity-mismatch reason absent"
pass "an RT object whose R2 size disagrees with the local file fails"

# Current static GTFS was never mirrored to R2 (only an older one is there).
seed_healthy
export LS_static_8_="$fresh $static8_size static/8/gtfs_static_20260801.zip"$'\n'
run_verify
[ "$rc" -eq 1 ] || fail "an un-mirrored current static object should exit 1, got $rc"
grep -q "has not been mirrored to R2 yet" "$TEST_BASE/out.log" || fail "un-mirrored-static reason absent"
pass "the current static GTFS missing from R2 fails"

# Current static object present in R2 but with a mismatched byte size.
seed_healthy
export LS_static_8_="$fresh 3 static/8/gtfs_static_20260909.zip"$'\n'
run_verify
[ "$rc" -eq 1 ] || fail "a static size mismatch should exit 1, got $rc"
grep -q "integrity check failed" "$TEST_BASE/out.log" || fail "static integrity-mismatch reason absent"
pass "a static object whose R2 size disagrees with the local file fails"

# An implausibly small (but size-matching) static object still fails the
# absolute floor check, independent of the local comparison.
seed_healthy
printf 'x' > "$COLLECTOR_BASE/data/8/static/gtfs_static_20260909.zip"
export LS_static_8_="$fresh 1 static/8/gtfs_static_20260909.zip"$'\n'
run_verify
[ "$rc" -eq 1 ] || fail "a tiny static object should exit 1, got $rc"
grep -q "bytes (minimum" "$TEST_BASE/out.log" || fail "tiny-object reason absent"
pass "a static object smaller than the configured floor fails"

# No agencies configured at all: nothing to verify is itself suspicious.
seed_healthy
printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
run_verify
[ "$rc" -eq 1 ] || fail "an empty roster should exit 1, got $rc"
grep -q "nothing to verify" "$TEST_BASE/out.log" || fail "empty-roster reason absent"
pass "an empty agencies roster fails instead of vacuously passing"

# Missing roster: cannot check anything.
seed_healthy
rm -f "$COLLECTOR_BASE/etc/agencies.tsv"
run_verify
[ "$rc" -eq 64 ] || fail "a missing roster should exit 64, got $rc"
pass "a missing agencies roster exits 64"

# A non-numeric threshold is rejected before it reaches any comparison.
seed_healthy
printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
set +e
R2_RT_MAX_STALE_DAYS=soon ../bin/verify-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "a non-numeric R2_RT_MAX_STALE_DAYS should exit 64, got $rc"
pass "a non-numeric threshold is rejected with exit 64"

set +e
VERIFY_R2_SYNC_WAIT_SECONDS=soon ../bin/verify-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "a non-numeric VERIFY_R2_SYNC_WAIT_SECONDS should exit 64, got $rc"
pass "a non-numeric sync wait is rejected with exit 64"

# A non-degenerate leading-zero threshold ("010") must be read as decimal 10,
# not octal 8 -- a 9-day-old RT object must still be considered fresh at a
# 10-day limit, but would wrongly fail if the limit were silently misread as
# an 8-day one. Single-agency (no static_url) roster keeps this focused on
# the RT threshold arithmetic.
printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
nine_days_ago=$(days_ago_ts 9)
export LS_rt_1_="$nine_days_ago $rt1_size rt/1/20260902.tar.gz"$'\n'
set +e
R2_RT_MAX_STALE_DAYS=010 ../bin/verify-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "R2_RT_MAX_STALE_DAYS=010 should treat a 9-day-old RT object as fresh under a decimal 10-day limit, got rc=$rc: $(cat "$TEST_BASE/out.log")"
grep -q "1 agencies checked" "$TEST_BASE/out.log" || fail "R2_RT_MAX_STALE_DAYS=010 did not finish checking the agency"
pass "a leading-zero threshold (010) is read as decimal 10, not octal 8"

# A leading-zero threshold with an invalid octal digit ("008") must not crash
# the arithmetic that consumes it and silently abort the rest of the
# per-agency loop while still exiting 0.
export LS_rt_1_="$fresh $rt1_size rt/1/20260909.tar.gz"$'\n'
set +e
R2_RT_MAX_STALE_DAYS=008 ../bin/verify-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "R2_RT_MAX_STALE_DAYS=008 with fresh objects should still exit 0, got rc=$rc: $(cat "$TEST_BASE/out.log")"
grep -q "value too great for base" "$TEST_BASE/out.log" \
    && fail "R2_RT_MAX_STALE_DAYS=008 triggered an octal-parse arithmetic error"
grep -q "1 agencies checked" "$TEST_BASE/out.log" \
    || fail "R2_RT_MAX_STALE_DAYS=008 aborted the per-agency loop early instead of checking the agency"
pass "a leading-zero threshold whose octal reading is invalid (008) doesn't abort the per-agency scan"

# hold_sync_lock <lock-file> <seconds> [<fixture-var> <fixture-line>]
# Stands in for an in-flight sync-r2.sh: holds <lock-file> for <seconds>,
# then, when given, finishes its upload by publishing <fixture-line> as the
# R2 listing for <fixture-var> before releasing the lock.
hold_sync_lock() {
    local lock="$1" seconds="$2" var="${3:-}" line="${4:-}"
    rm -f "$TEST_BASE/lock-held"
    (
        exec 9>"$lock"
        flock 9
        touch "$TEST_BASE/lock-held"
        sleep "$seconds"
        [ -z "$var" ] || printf '%s\n' "$line" > "$LS_FIXTURE_DIR/$var"
    ) &
    holder_pid=$!
    for _ in $(seq 1 100); do
        [ -f "$TEST_BASE/lock-held" ] && return 0
        sleep 0.05
    done
    fail "background lock holder never acquired $lock (test setup issue)"
}

# verify-r2.sh runs while sync-r2.sh may still be uploading, so it must wait
# for sync-r2.sh's lock before listing R2, and must not pass when that wait
# runs out. Where `flock` is absent (e.g. macOS) every other test here
# already covers the run-without-waiting path; this block skips cleanly.
if command -v flock >/dev/null 2>&1; then
    export LS_FIXTURE_DIR="$TEST_BASE/ls-fixtures"
    mkdir -p "$LS_FIXTURE_DIR"
    printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' \
        > "$COLLECTOR_BASE/etc/agencies.tsv"
    printf '1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
        >> "$COLLECTOR_BASE/etc/agencies.tsv"
    printf '8\thiroden\t60\thttp://feed.test/tu8.pb\thttp://feed.test/s8.zip\thttp://ping.test/8\n' \
        >> "$COLLECTOR_BASE/etc/agencies.tsv"
    head -c 2048 /dev/zero > "$COLLECTOR_BASE/data/8/static/gtfs_static_20260909.zip"

    # Mid-sync, R2 still lacks agency 8's current static zip; the sync
    # uploads it and then releases the lock.
    seed_healthy
    export LS_static_8_="$fresh $static8_size static/8/gtfs_static_20260801.zip"$'\n'
    hold_sync_lock "$COLLECTOR_BASE/sync-r2.lock" 1 \
        LS_static_8_ "$fresh $static8_size static/8/gtfs_static_20260909.zip"
    export VERIFY_R2_SYNC_WAIT_SECONDS=30
    run_verify
    wait "$holder_pid"
    rm -f "$LS_FIXTURE_DIR/LS_static_8_"
    [ "$rc" -eq 0 ] \
        || fail "verify-r2.sh should wait for the in-flight sync, then pass, got rc=$rc: $(cat "$TEST_BASE/out.log")"
    pass "verify-r2.sh waits for an in-flight sync-r2.sh before listing R2"

    # A sync still running when the wait runs out is a problem, not a pass,
    # and R2 is not listed at all. Holding a non-default SYNC_R2_LOCK also
    # proves verify-r2.sh waits on the same lock file sync-r2.sh uses.
    seed_healthy
    export SYNC_R2_LOCK="$TEST_BASE/custom-sync.lock"
    hold_sync_lock "$SYNC_R2_LOCK" 4
    export VERIFY_R2_SYNC_WAIT_SECONDS=1
    run_verify
    wait "$holder_pid"
    unset SYNC_R2_LOCK VERIFY_R2_SYNC_WAIT_SECONDS
    [ "$rc" -eq 1 ] || fail "a sync still running past the wait should exit 1, got $rc: $(cat "$TEST_BASE/out.log")"
    grep -q "PROBLEM sync-r2.sh still running" "$TEST_BASE/out.log" \
        || fail "timeout reason absent: $(cat "$TEST_BASE/out.log")"
    [ -s "$AWS_LOG" ] && fail "R2 was listed while sync-r2.sh was still running"
    read -r _ marker_result _ < "$COLLECTOR_BASE/.verify-r2.last-result"
    [ "$marker_result" = "fail" ] || fail "a timed-out wait should record result=fail, got '$marker_result'"
    pass "a sync-r2.sh still running past the wait fails verify-r2.sh without listing R2"
else
    echo "SKIP: flock not installed on this platform, sync-r2 wait behavior not exercised"
fi
