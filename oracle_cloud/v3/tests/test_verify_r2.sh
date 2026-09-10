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
# after the sanitized prefix it was asked to list.
export AWS_LOG="$TEST_BASE/aws.log"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    # args: s3 ls s3://bucket/PREFIX --recursive --endpoint-url URL
    url="$3"
    prefix="${url#s3://*/}"
    var="LS_$(printf '%s' "$prefix" | tr -c 'A-Za-z0-9' '_')"
    printf '%s' "${!var:-}"
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
