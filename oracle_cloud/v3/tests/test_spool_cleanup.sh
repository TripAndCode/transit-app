#!/usr/bin/env bash
# spool-cleanup.sh: reclaims local RT/static archives the moment R2 actually
# confirms (by listing + byte-size match) they were uploaded, leaves
# failed/partial uploads and the static latest.zip target in place, and
# reports a failure (never a further deletion) when what remains exceeds the
# explicit disk budget.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

export OBJECT_STORE_ENDPOINT="https://example.r2.cloudflarestorage.com"
export OBJECT_STORE_BUCKET="test-bucket"
export OBJECT_STORE_ACCESS_KEY_ID="AKIDTEST"
export OBJECT_STORE_SECRET_ACCESS_KEY="secrettest"
export SPOOL_DISK_BUDGET_BYTES=1000000

# LS_<sanitized-prefix> env vars feed canned `aws s3 ls --recursive` output,
# matching test_verify_r2.sh's/test_prune_r2.sh's shim convention.
export AWS_LOG="$TEST_BASE/aws.log"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    url="$3"
    prefix="${url#s3://*/}"
    var="LS_$(printf '%s' "$prefix" | tr -c 'A-Za-z0-9' '_')"
    printf '%s' "${!var:-}"
    exit 0
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

now_ts() { date -u +%Y-%m-%d\ %H:%M:%S; }

run_cleanup() {
    : > "$AWS_LOG"
    set +e
    ../bin/spool-cleanup.sh > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}

# --- required env ------------------------------------------------------
if OBJECT_STORE_ENDPOINT= OBJECT_STORE_BUCKET= OBJECT_STORE_ACCESS_KEY_ID= \
    OBJECT_STORE_SECRET_ACCESS_KEY= ../bin/spool-cleanup.sh 2>/dev/null; then
    fail "spool-cleanup.sh should fail when OBJECT_STORE_* is unset"
fi
pass "missing OBJECT_STORE_* fails closed"

if SPOOL_DISK_BUDGET_BYTES= ../bin/spool-cleanup.sh 2>/dev/null; then
    fail "spool-cleanup.sh should fail when SPOOL_DISK_BUDGET_BYTES is unset"
fi
pass "missing SPOOL_DISK_BUDGET_BYTES fails closed"

if SPOOL_DISK_BUDGET_BYTES=notanumber ../bin/spool-cleanup.sh 2>/dev/null; then
    fail "spool-cleanup.sh should reject a non-numeric SPOOL_DISK_BUDGET_BYTES"
fi
pass "non-numeric SPOOL_DISK_BUDGET_BYTES is rejected"

if SPOOL_DISK_BUDGET_BYTES=0 ../bin/spool-cleanup.sh 2>/dev/null; then
    fail "spool-cleanup.sh should reject a zero SPOOL_DISK_BUDGET_BYTES"
fi
pass "zero SPOOL_DISK_BUDGET_BYTES is rejected"

# Missing roster.
if AGENCIES_TSV=/does/not/exist ../bin/spool-cleanup.sh 2>/dev/null; then
    fail "spool-cleanup.sh should exit nonzero with a missing roster"
fi
pass "a missing agencies roster fails"

# --- fixtures ------------------------------------------------------------
rt1="$COLLECTOR_BASE/data/1/rt"; rt8="$COLLECTOR_BASE/data/8/rt"
sdir8="$COLLECTOR_BASE/data/8/static"
mkdir -p "$rt1" "$rt8" "$sdir8"

# a1: one RT tarball already fully mirrored (verified -> should be removed).
printf 'RTDATA-1-UPLOADED' > "$rt1/20260901.tar.gz"
size_a1_uploaded=$(wc -c < "$rt1/20260901.tar.gz" | tr -d ' ')

# a1: one RT tarball never mirrored (missing from R2 -> must survive).
printf 'RTDATA-1-PENDING' > "$rt1/20260902.tar.gz"

# a8: one RT tarball mirrored but truncated in R2 (size mismatch -> survives).
printf 'RTDATA-8-CORRUPT' > "$rt8/20260901.tar.gz"

# a8: static — a non-latest zip already mirrored (should be removed), and the
# current latest.zip target, which must survive even though R2 also has a
# byte-identical copy of it (never delete the live pointer).
printf 'STATICZIP-OLD' > "$sdir8/gtfs_static_20260801.zip"
size_static_old=$(wc -c < "$sdir8/gtfs_static_20260801.zip" | tr -d ' ')
printf 'STATICZIP-CURRENT' > "$sdir8/gtfs_static_20260901.zip"
size_static_current=$(wc -c < "$sdir8/gtfs_static_20260901.zip" | tr -d ' ')
ln -sfn gtfs_static_20260901.zip "$sdir8/latest.zip"

fresh=$(now_ts)
export LS_rt_1_="$fresh $size_a1_uploaded rt/1/20260901.tar.gz"$'\n'
export LS_rt_8_="$fresh 3 rt/8/20260901.tar.gz"$'\n'
export LS_static_8_="$fresh $size_static_old static/8/gtfs_static_20260801.zip
$fresh $size_static_current static/8/gtfs_static_20260901.zip
"

run_cleanup
[ "$rc" -eq 0 ] || fail "spool-cleanup should exit 0 under budget: $(cat "$TEST_BASE/out.log")"

[ -f "$rt1/20260901.tar.gz" ] && fail "a1: verified-uploaded RT tarball survived cleanup"
[ -f "$rt1/20260902.tar.gz" ] || fail "a1: never-uploaded RT tarball was deleted"
[ -f "$rt8/20260901.tar.gz" ] || fail "a8: size-mismatched RT tarball was deleted"
[ -f "$sdir8/gtfs_static_20260801.zip" ] && fail "a8: verified-uploaded old static zip survived cleanup"
[ -f "$sdir8/gtfs_static_20260901.zip" ] || fail "a8: current latest.zip target was deleted"
grep -q "kept — R2 object size" "$TEST_BASE/out.log" || fail "size-mismatch reason missing from output"
grep -q "kept — not yet found in R2" "$TEST_BASE/out.log" || fail "not-yet-uploaded reason missing from output"
pass "removes only R2-verified archives, keeps pending/corrupt uploads and the live static target"

# --- idempotency ---------------------------------------------------------
run_cleanup
[ "$rc" -eq 0 ] || fail "a second run should still exit 0: $(cat "$TEST_BASE/out.log")"
[ -f "$rt1/20260902.tar.gz" ] || fail "second run should not touch the still-pending tarball"
pass "a second run is idempotent (nothing left to reclaim errors, nothing extra removed)"

# --- disk budget -----------------------------------------------------------
# Force the remaining (never-uploaded / mismatched) local bytes over a tiny
# budget: cleanup must report failure but must NOT delete anything further.
run_budget() {
    : > "$AWS_LOG"
    set +e
    SPOOL_DISK_BUDGET_BYTES=1 ../bin/spool-cleanup.sh > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}
run_budget
[ "$rc" -eq 1 ] || fail "exceeding the disk budget should exit 1, got $rc: $(cat "$TEST_BASE/out.log")"
grep -q "PROBLEM local spool is" "$TEST_BASE/out.log" || fail "budget-exceeded reason missing from output"
[ -f "$rt1/20260902.tar.gz" ] || fail "budget overage must not cause deletion of an unverified file"
[ -f "$rt8/20260901.tar.gz" ] || fail "budget overage must not cause deletion of a mismatched file"
pass "exceeding the disk budget fails loudly without deleting unverified archives"
