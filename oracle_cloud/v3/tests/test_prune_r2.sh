#!/usr/bin/env bash
# prune-r2.sh: enforces a bounded (if generous) retention window on R2
# objects, keeping the current static target regardless of age, and refuses
# to run unless sync-r2.sh's success marker is fresh -- mirroring prune.sh's
# own local-disk safety gate but against R2.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

export OBJECT_STORE_ENDPOINT="https://example.r2.cloudflarestorage.com"
export OBJECT_STORE_BUCKET="test-bucket"
export OBJECT_STORE_ACCESS_KEY_ID="AKIDTEST"
export OBJECT_STORE_SECRET_ACCESS_KEY="secrettest"

# "Old" relative to the 1-day retention used below and to local prune.sh's
# 90-day default, but well inside prune-r2.sh's own multi-year defaults --
# the default-retention test below depends on that.
old_ts=$(date -u -v-400d +%Y-%m-%d\ %H:%M:%S 2>/dev/null || date -u -d "400 days ago" +%Y-%m-%d\ %H:%M:%S)
# Younger than the 1-day retention used below, but still clearly not "now".
new_ts=$(date -u -v-6H +%Y-%m-%d\ %H:%M:%S 2>/dev/null || date -u -d "6 hours ago" +%Y-%m-%d\ %H:%M:%S)

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

mkdir -p "$COLLECTOR_BASE/data/8/static"
ln -sfn gtfs_static_20200101.zip "$COLLECTOR_BASE/data/8/static/latest.zip"

seed_lists() {
    export LS_rt_1_="$old_ts 100 rt/1/20200101.tar.gz
$new_ts 100 rt/1/20260901.tar.gz
"
    # The old static object here is the CURRENT target (latest.zip points at
    # it) -- must survive despite being far past R2_STATIC_RETENTION_DAYS.
    export LS_static_8_="$old_ts 200 static/8/gtfs_static_20200101.zip
$new_ts 200 static/8/gtfs_static_20260901.zip
"
}

# Zero is not a positive integer despite passing a naive digits-only check --
# R2_RT_RETENTION_DAYS=0 must not be accepted, since cutoff=NOW would delete
# every RT object ever uploaded (there is no "keep" exception on the RT path).
seed_lists
set +e
R2_RT_RETENTION_DAYS=0 R2_STATIC_RETENTION_DAYS=1 ../bin/prune-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "R2_RT_RETENTION_DAYS=0 should exit 64, got $rc"
grep -q "R2_RT_RETENTION_DAYS must be a positive integer" "$TEST_BASE/out.log" \
    || fail "rejection reason for R2_RT_RETENTION_DAYS=0 absent"
[ -s "$AWS_LOG" ] && fail "aws was invoked despite R2_RT_RETENTION_DAYS=0"
pass "R2_RT_RETENTION_DAYS=0 is rejected instead of wiping the RT archive"

# A leading-zero numeral like "00" passes a naive digits-only check too, but
# a plain (non-base-10-forced) arithmetic context parses it as octal, where
# "00" is still 0 -- the same catastrophic cutoff=NOW bug as R2_RT_RETENTION_
# DAYS=0 above, just spelled differently. Validation must reject it too.
seed_lists
set +e
R2_RT_RETENTION_DAYS=00 R2_STATIC_RETENTION_DAYS=1 ../bin/prune-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "R2_RT_RETENTION_DAYS=00 should exit 64, got $rc"
grep -q "R2_RT_RETENTION_DAYS must be a positive integer" "$TEST_BASE/out.log" \
    || fail "rejection reason for R2_RT_RETENTION_DAYS=00 absent"
[ -s "$AWS_LOG" ] && fail "aws was invoked despite R2_RT_RETENTION_DAYS=00"
pass "R2_RT_RETENTION_DAYS=00 is rejected instead of wiping the RT archive"

set +e
R2_RT_RETENTION_DAYS=1 R2_STATIC_RETENTION_DAYS=0 ../bin/prune-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "R2_STATIC_RETENTION_DAYS=0 should exit 64, got $rc"
grep -q "R2_STATIC_RETENTION_DAYS must be a positive integer" "$TEST_BASE/out.log" \
    || fail "rejection reason for R2_STATIC_RETENTION_DAYS=0 absent"
[ -s "$AWS_LOG" ] && fail "aws was invoked despite R2_STATIC_RETENTION_DAYS=0"
pass "R2_STATIC_RETENTION_DAYS=0 is rejected instead of wiping the static archive"

# No sync-r2.sh success marker yet -> refuses to run, deletes nothing.
seed_lists
if R2_RT_RETENTION_DAYS=1 R2_STATIC_RETENTION_DAYS=1 ../bin/prune-r2.sh 2>/dev/null; then
    fail "prune-r2.sh should refuse to run without a sync-r2.sh success marker"
fi
[ -s "$AWS_LOG" ] && fail "aws was invoked despite missing marker"
pass "prune-r2.sh refuses to run when the sync-r2.sh marker is missing"

# Stale marker (older than SYNC_R2_MAX_STALE_DAYS) -> also refuses.
old_marker_ts=$(date -u -v-30d +%Y%m%d%H%M 2>/dev/null || date -d "30 days ago" +%Y%m%d%H%M)
touch -t "$old_marker_ts" "$COLLECTOR_BASE/.sync-r2.last-ok"
if R2_RT_RETENTION_DAYS=1 R2_STATIC_RETENTION_DAYS=1 ../bin/prune-r2.sh 2>/dev/null; then
    fail "prune-r2.sh should refuse to run when the marker is stale"
fi
[ -s "$AWS_LOG" ] && fail "aws was invoked despite a stale marker"
pass "prune-r2.sh refuses to run when the sync-r2.sh marker is stale"

# A non-degenerate leading-zero retention ("010") must be read as decimal 10,
# not octal 8 -- an RT object 9 days old must survive at retention 10 but
# would wrongly be pruned if the retention were silently misread as octal 8.
nine_days_ts=$(date -u -v-9d +%Y-%m-%d\ %H:%M:%S 2>/dev/null || date -u -d "9 days ago" +%Y-%m-%d\ %H:%M:%S)
export LS_rt_1_="$nine_days_ts 100 rt/1/20260902.tar.gz
"
export LS_static_8_="$old_ts 200 static/8/gtfs_static_20200101.zip
"
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"
R2_RT_RETENTION_DAYS=010 R2_STATIC_RETENTION_DAYS=3650 ../bin/prune-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
[ "$rc" -eq 0 ] || fail "R2_RT_RETENTION_DAYS=010 should run cleanly, got rc=$rc: $(cat "$TEST_BASE/out.log")"
grep -q "s3 rm s3://test-bucket/rt/1/20260902.tar.gz" "$AWS_LOG" \
    && fail "a 9-day-old RT object was pruned under retention=010 (octal-8 misreading); it should survive under decimal 10"
pass "a leading-zero retention (010) is read as decimal 10, not octal 8"

# A leading-zero retention with an invalid octal digit ("018") must not crash
# the arithmetic that consumes it -- normalizing the variable to decimal at
# validation time is what prevents the fatal "value too great for base" error.
seed_lists
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"
R2_RT_RETENTION_DAYS=018 R2_STATIC_RETENTION_DAYS=3650 ../bin/prune-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
[ "$rc" -eq 0 ] || fail "R2_RT_RETENTION_DAYS=018 should not crash, got rc=$rc: $(cat "$TEST_BASE/out.log")"
grep -q "value too great for base" "$TEST_BASE/out.log" \
    && fail "R2_RT_RETENTION_DAYS=018 triggered an octal-parse arithmetic error"
grep -q "s3 rm s3://test-bucket/rt/1/20200101.tar.gz" "$AWS_LOG" \
    || fail "the old RT object should still be pruned under retention=018 (decimal 18)"
pass "a leading-zero retention with an invalid octal digit (018) is read as decimal, not crashed on"
seed_lists

# Fresh marker, aggressive (1-day) retention -> deletes old objects, keeps
# young ones and the current static target regardless of age.
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"
R2_RT_RETENTION_DAYS=1 R2_STATIC_RETENTION_DAYS=1 ../bin/prune-r2.sh >/dev/null

grep -q "s3 rm s3://test-bucket/rt/1/20200101.tar.gz" "$AWS_LOG" \
    || fail "old RT object was not deleted"
grep -q "s3 rm s3://test-bucket/rt/1/20260901.tar.gz" "$AWS_LOG" \
    && fail "young RT object was deleted"
grep -q "s3 rm s3://test-bucket/static/8/gtfs_static_20200101.zip" "$AWS_LOG" \
    && fail "the current static target was deleted despite its age"
grep -q "s3 rm s3://test-bucket/static/8/gtfs_static_20260901.zip" "$AWS_LOG" \
    && fail "a non-current young static object was deleted"
grep -q -- "--endpoint-url https://example.r2.cloudflarestorage.com" "$AWS_LOG" \
    || fail "delete calls missing --endpoint-url (would silently hit real AWS S3 instead of R2)"
pass "old objects are pruned, young ones and the current static target survive"

# Default (generous, multi-year) retention deletes nothing at all.
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"
../bin/prune-r2.sh >/dev/null
grep -q "s3 rm" "$AWS_LOG" && fail "default retention deleted something it shouldn't have"
pass "the default retention window is generous enough to delete nothing here"

# A delete failure for one object must not abort the rest of the run, but
# the script must still exit nonzero so cron/monitoring can see it.
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"
if AWS_RM_EXIT=1 R2_RT_RETENTION_DAYS=1 R2_STATIC_RETENTION_DAYS=1 ../bin/prune-r2.sh >/dev/null 2>&1; then
    fail "prune-r2.sh should exit nonzero when a delete failed"
fi
grep -q "s3 rm s3://test-bucket/rt/1/20200101.tar.gz" "$AWS_LOG" \
    || fail "the delete was not attempted despite the induced failure"
pass "a delete failure is surfaced as a nonzero exit without aborting the run"

# When an agency's local latest.zip cannot be resolved (no symlink present),
# the only known static object for that agency in R2 -- even one well past
# retention -- must survive: with no live target to compare against, pruning
# has no way to tell the current copy from a stale one, so it must not touch
# that agency's static objects at all rather than delete everything.
seed_lists
rm -f "$COLLECTOR_BASE/data/8/static/latest.zip"
export LS_static_8_="$old_ts 200 static/8/gtfs_static_20200101.zip
"
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"
R2_RT_RETENTION_DAYS=1 R2_STATIC_RETENTION_DAYS=1 ../bin/prune-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
[ "$rc" -eq 0 ] || fail "prune-r2.sh should still exit 0 when only latest.zip is unresolved, got rc=$rc: $(cat "$TEST_BASE/out.log")"
grep -q "s3 rm s3://test-bucket/static/8/gtfs_static_20200101.zip" "$AWS_LOG" \
    && fail "a static object was deleted for an agency whose live target (latest.zip) could not be resolved"
pass "static pruning is skipped for an agency whose local latest.zip cannot be resolved"
ln -sfn gtfs_static_20200101.zip "$COLLECTOR_BASE/data/8/static/latest.zip"

# Missing roster: cannot enumerate agencies to prune.
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
rm -f "$COLLECTOR_BASE/etc/agencies.tsv"
if ../bin/prune-r2.sh 2>/dev/null; then
    fail "prune-r2.sh should refuse to run without an agencies roster"
fi
pass "prune-r2.sh exits nonzero when the agencies roster is missing"
