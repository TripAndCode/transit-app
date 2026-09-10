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

# Missing roster: cannot enumerate agencies to prune.
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
rm -f "$COLLECTOR_BASE/etc/agencies.tsv"
if ../bin/prune-r2.sh 2>/dev/null; then
    fail "prune-r2.sh should refuse to run without an agencies roster"
fi
pass "prune-r2.sh exits nonzero when the agencies roster is missing"
