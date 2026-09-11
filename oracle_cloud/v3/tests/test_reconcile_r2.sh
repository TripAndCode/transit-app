#!/usr/bin/env bash
# reconcile-r2.sh: classifies every object in the bucket as "expected" (a
# known-roster agency id, filename matching the rt/<id>/<YYYYMMDD>.tar.gz or
# static/<id>/gtfs_static_<YYYYMMDD>.zip naming rule) or "orphan", reports
# orphans by default, and only deletes them with --execute plus a fresh
# sync-r2.sh marker -- reconfirming each one individually right before its
# delete.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

export OBJECT_STORE_ENDPOINT="https://example.r2.cloudflarestorage.com"
export OBJECT_STORE_BUCKET="test-bucket"
export OBJECT_STORE_ACCESS_KEY_ID="AKIDTEST"
export OBJECT_STORE_SECRET_ACCESS_KEY="secrettest"

printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
printf '1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"
printf '8\thiroden\t60\thttp://feed.test/tu8.pb\thttp://feed.test/s8.zip\thttp://ping.test/8\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"

# LS_ALL feeds every `aws s3 ls [--recursive] s3://bucket/<prefix>` call: the
# shim filters LS_ALL's lines to those whose key (4th field) starts with
# <prefix>, the same semantics real S3 prefix listing has -- so the exact
# same fixture also answers reconcile-r2.sh's per-key recheck calls
# (prefix == the full key) without a second set of env vars per test.
export AWS_LOG="$TEST_BASE/aws.log"
export RM_LOG="$TEST_BASE/rm.log"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    url="$3"
    prefix="${url#s3://*/}"
    printf '%s\n' "$LS_ALL" | awk -v p="$prefix" '$4 != "" && index($4, p) == 1'
    exit 0
fi
if [ "$1" = s3 ] && [ "$2" = rm ]; then
    key="${3#s3://*/}"
    echo "$key" >> "$RM_LOG"
    exit "${AWS_RM_EXIT:-0}"
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"

seed_listing() {
    export LS_ALL="2026-08-01 00:00:00 111 rt/1/20260801.tar.gz
2026-09-01 00:00:00 112 rt/1/20260901.tar.gz
2026-09-01 00:00:00 999 rt/1/20260901.tar.gz.tmp
2026-09-01 00:00:00 222 static/8/gtfs_static_20260901.zip
2026-09-01 00:00:00 333 static/8/notes.txt
2026-09-01 00:00:00 444 rt/99/20260901.tar.gz
2026-09-01 00:00:00 555 backups/whatever.zip"
}

run() {
    : > "$AWS_LOG"; : > "$RM_LOG"
    set +e
    ../bin/reconcile-r2.sh "$@" > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}

# --- required env / config -----------------------------------------------
seed_listing
if OBJECT_STORE_ENDPOINT= OBJECT_STORE_BUCKET= OBJECT_STORE_ACCESS_KEY_ID= \
    OBJECT_STORE_SECRET_ACCESS_KEY= ../bin/reconcile-r2.sh 2>/dev/null; then
    fail "reconcile-r2.sh should fail when OBJECT_STORE_* is unset"
fi
pass "missing OBJECT_STORE_* fails closed"

rm -f "$COLLECTOR_BASE/etc/agencies.tsv"
if ../bin/reconcile-r2.sh 2>/dev/null; then
    fail "reconcile-r2.sh should refuse to run without an agencies roster"
fi
pass "reconcile-r2.sh exits nonzero when the agencies roster is missing"

printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
printf '1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"
printf '8\thiroden\t60\thttp://feed.test/tu8.pb\thttp://feed.test/s8.zip\thttp://ping.test/8\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"

run --bogus-flag
[ "$rc" -eq 64 ] || fail "an unknown argument should exit 64, got $rc"
pass "an unknown argument is rejected"

# --- dry run (default) -----------------------------------------------------
run
[ "$rc" -eq 1 ] || fail "dry run with orphans present should exit 1, got $rc: $(cat "$TEST_BASE/out.log")"
[ -s "$RM_LOG" ] && fail "dry run must never call s3 rm"
grep -q "ORPHAN rt/99/20260901.tar.gz" "$TEST_BASE/out.log" \
    || fail "an object under an agency id absent from the roster was not flagged"
grep -q "ORPHAN rt/1/20260901.tar.gz.tmp" "$TEST_BASE/out.log" \
    || fail "a filename not matching <YYYYMMDD>.tar.gz was not flagged"
grep -q "ORPHAN static/8/notes.txt" "$TEST_BASE/out.log" \
    || fail "a filename not matching gtfs_static_<YYYYMMDD>.zip was not flagged"
grep -q "ORPHAN backups/whatever.zip" "$TEST_BASE/out.log" \
    || fail "a key outside the rt/<id>/ and static/<id>/ layout was not flagged"
grep -q "ORPHAN rt/1/20260801.tar.gz" "$TEST_BASE/out.log" \
    && fail "a correctly-named object for a known agency was flagged as an orphan"
grep -q "ORPHAN rt/1/20260901.tar.gz " "$TEST_BASE/out.log" \
    && fail "a correctly-named object for a known agency was flagged as an orphan"
grep -q "ORPHAN static/8/gtfs_static_20260901.zip" "$TEST_BASE/out.log" \
    && fail "a correctly-named static object for a known agency was flagged as an orphan"
pass "dry run reports every orphan category and deletes nothing"

# --- --execute without a sync-r2.sh marker: refuses --------------------------
run --execute
[ "$rc" -eq 65 ] || fail "--execute without a fresh marker should exit 65, got $rc"
[ -s "$RM_LOG" ] && fail "--execute ran a delete despite the missing marker"
pass "--execute refuses to delete without a fresh sync-r2.sh success marker"

# --- --execute with a stale marker: refuses ---------------------------------
old_marker_ts=$(date -u -v-30d +%Y%m%d%H%M 2>/dev/null || date -d "30 days ago" +%Y%m%d%H%M)
touch -t "$old_marker_ts" "$COLLECTOR_BASE/.sync-r2.last-ok"
run --execute
[ "$rc" -eq 65 ] || fail "--execute with a stale marker should exit 65, got $rc"
[ -s "$RM_LOG" ] && fail "--execute ran a delete despite a stale marker"
pass "--execute refuses to delete with a stale sync-r2.sh success marker"

# --- --execute with a fresh marker: deletes only orphans --------------------
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
run --execute
[ "$rc" -eq 0 ] || fail "--execute with a fresh marker and no delete failures should exit 0, got $rc: $(cat "$TEST_BASE/out.log")"
grep -q "^rt/99/20260901.tar.gz$" "$RM_LOG" || fail "unknown-agency object was not deleted"
grep -q "^rt/1/20260901.tar.gz.tmp$" "$RM_LOG" || fail "malformed rt filename was not deleted"
grep -q "^static/8/notes.txt$" "$RM_LOG" || fail "malformed static filename was not deleted"
grep -q "^backups/whatever.zip$" "$RM_LOG" || fail "out-of-layout key was not deleted"
grep -q "^rt/1/20260801.tar.gz$" "$RM_LOG" && fail "a correctly-named rt object was deleted"
grep -q "^rt/1/20260901.tar.gz$" "$RM_LOG" && fail "a correctly-named rt object was deleted"
grep -q "^static/8/gtfs_static_20260901.zip$" "$RM_LOG" && fail "a correctly-named static object was deleted"
[ "$(wc -l < "$RM_LOG" | tr -d ' ')" -eq 4 ] || fail "expected exactly 4 deletes, got: $(cat "$RM_LOG")"
grep -q -- "--endpoint-url https://example.r2.cloudflarestorage.com" "$AWS_LOG" \
    || fail "delete calls missing --endpoint-url (would silently hit real AWS S3 instead of R2)"
pass "--execute with a fresh marker deletes exactly the orphans, nothing else"

# RECONCILE_R2_EXECUTE=1 is the flag's cron-friendly env equivalent.
seed_listing
: > "$AWS_LOG"; : > "$RM_LOG"
RECONCILE_R2_EXECUTE=1 ../bin/reconcile-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
[ "$rc" -eq 0 ] || fail "RECONCILE_R2_EXECUTE=1 should behave like --execute, got $rc"
[ "$(wc -l < "$RM_LOG" | tr -d ' ')" -eq 4 ] || fail "RECONCILE_R2_EXECUTE=1 did not delete the expected orphans"
pass "RECONCILE_R2_EXECUTE=1 behaves like --execute"

# --- clean bucket: nothing to report or delete ------------------------------
export LS_ALL="2026-09-01 00:00:00 112 rt/1/20260901.tar.gz
2026-09-01 00:00:00 222 static/8/gtfs_static_20260901.zip"
run
[ "$rc" -eq 0 ] || fail "dry run with no orphans should exit 0, got $rc"
[ -s "$RM_LOG" ] && fail "a clean bucket triggered a delete"
pass "a bucket with no orphans exits 0 and changes nothing"

run --execute
[ "$rc" -eq 0 ] || fail "--execute with no orphans should exit 0, got $rc"
[ -s "$RM_LOG" ] && fail "--execute deleted something from a clean bucket"
pass "--execute on a clean bucket deletes nothing and exits 0"

# --- a delete race: object changed between listing and delete --------------
seed_listing
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"; : > "$RM_LOG"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    url="$3"
    prefix="${url#s3://*/}"
    if [ "$prefix" = "rt/99/20260901.tar.gz" ]; then
        # Simulate the object changing size between the bucket-wide listing
        # and this per-object recheck (e.g. a racing re-upload).
        echo "2026-09-01 00:00:00 999999 rt/99/20260901.tar.gz"
        exit 0
    fi
    printf '%s\n' "$LS_ALL" | awk -v p="$prefix" '$4 != "" && index($4, p) == 1'
    exit 0
fi
if [ "$1" = s3 ] && [ "$2" = rm ]; then
    key="${3#s3://*/}"
    echo "$key" >> "$RM_LOG"
    exit "${AWS_RM_EXIT:-0}"
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"
set +e
../bin/reconcile-r2.sh --execute > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "a recheck size mismatch should surface as a nonzero exit, got $rc: $(cat "$TEST_BASE/out.log")"
grep -q "^rt/99/20260901.tar.gz$" "$RM_LOG" && fail "an object that changed between listing and delete was deleted anyway"
grep -q "changed size between listing and delete" "$TEST_BASE/out.log" \
    || fail "the recheck mismatch was not reported"
pass "an object that changed between listing and delete is skipped, not deleted, and surfaces as a failure"

# --- a recheck prefix match that also returns an unrelated longer key ------
# `aws s3 ls <prefix>` for prefix="rt/1/20260901.tar.gz" also matches a key
# that merely STARTS WITH it, e.g. "rt/1/20260901.tar.gz.tmp" -- the recheck
# must pick out the exact key, not just trust the first line returned.
seed_listing
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"; : > "$RM_LOG"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    url="$3"
    prefix="${url#s3://*/}"
    if [ "$prefix" = "rt/1/20260901.tar.gz.tmp" ]; then
        printf '%s\n' "2026-09-01 00:00:00 999 rt/1/20260901.tar.gz.tmp"
        exit 0
    fi
    printf '%s\n' "$LS_ALL" | awk -v p="$prefix" '$4 != "" && index($4, p) == 1'
    exit 0
fi
if [ "$1" = s3 ] && [ "$2" = rm ]; then
    key="${3#s3://*/}"
    echo "$key" >> "$RM_LOG"
    exit "${AWS_RM_EXIT:-0}"
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"
run --execute
[ "$rc" -eq 0 ] || fail "recheck prefix collision should not surface as a failure, got $rc: $(cat "$TEST_BASE/out.log")"
grep -q "^rt/1/20260901.tar.gz.tmp$" "$RM_LOG" || fail "the malformed .tmp object was not deleted despite an exact-size recheck match"
grep -q "^rt/1/20260901.tar.gz$" "$RM_LOG" && fail "the correctly-named object was deleted due to a prefix-match recheck collision"
pass "the recheck picks the exact key, not a longer key that merely shares its prefix"

# --- roster changed between the initial listing and the delete-time recheck -
# rt/99/... is orphaned at the initial bucket-wide listing (id 99 absent from
# the roster), but id 99 is re-added to the roster before this object's own
# delete-time recheck runs. known_ids must be rebuilt from the roster right
# before that recheck's classify call, not just once at the top of the run,
# or this reclassification is a no-op and the object gets deleted anyway.
seed_listing
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
: > "$AWS_LOG"; : > "$RM_LOG"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    url="$3"
    prefix="${url#s3://*/}"
    if [ "$prefix" = "rt/99/20260901.tar.gz" ]; then
        printf '99\tnewagency\t30\thttp://feed.test/tu99.pb\t\thttp://ping.test/99\n' \
            >> "$COLLECTOR_BASE/etc/agencies.tsv"
        echo "2026-09-01 00:00:00 444 rt/99/20260901.tar.gz"
        exit 0
    fi
    printf '%s\n' "$LS_ALL" | awk -v p="$prefix" '$4 != "" && index($4, p) == 1'
    exit 0
fi
if [ "$1" = s3 ] && [ "$2" = rm ]; then
    key="${3#s3://*/}"
    echo "$key" >> "$RM_LOG"
    exit "${AWS_RM_EXIT:-0}"
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"
run --execute
[ "$rc" -eq 0 ] || fail "a roster change caught on recheck should not surface as a failure, got $rc: $(cat "$TEST_BASE/out.log")"
grep -q "^rt/99/20260901.tar.gz$" "$RM_LOG" \
    && fail "an object whose agency id was re-added to the roster before the recheck was deleted anyway (known_ids was not rebuilt before the recheck)"
grep -q "rt/99/20260901.tar.gz reclassified as expected on recheck" "$TEST_BASE/out.log" \
    || fail "the roster-change reclassification message is missing"
pass "known_ids is rebuilt before the per-object recheck, so a roster change between listing and delete is caught"

# --- SYNC_R2_MAX_STALE_DAYS validation --------------------------------------
seed_listing
: > "$AWS_LOG"; : > "$RM_LOG"
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
    url="$3"
    prefix="${url#s3://*/}"
    printf '%s\n' "$LS_ALL" | awk -v p="$prefix" '$4 != "" && index($4, p) == 1'
    exit 0
fi
if [ "$1" = s3 ] && [ "$2" = rm ]; then
    key="${3#s3://*/}"
    echo "$key" >> "$RM_LOG"
    exit "${AWS_RM_EXIT:-0}"
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"

set +e
SYNC_R2_MAX_STALE_DAYS=nope ../bin/reconcile-r2.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "a non-numeric SYNC_R2_MAX_STALE_DAYS should exit 64, got $rc: $(cat "$TEST_BASE/out.log")"
grep -q "MAX_STALE_DAYS must be a positive integer" "$TEST_BASE/out.log" \
    || fail "rejection reason for a non-numeric SYNC_R2_MAX_STALE_DAYS is missing"
[ -s "$AWS_LOG" ] && fail "aws was invoked despite an invalid SYNC_R2_MAX_STALE_DAYS"
pass "a non-numeric SYNC_R2_MAX_STALE_DAYS is rejected instead of crashing the staleness arithmetic"

# A leading-zero numeral ("010") must be read as decimal 10, not octal 8: a
# marker 9 days old is fresh under a 10-day allowance but stale under an
# 8-day one, so misreading "010" as octal would wrongly refuse to run.
nine_days_marker_ts=$(date -u -v-9d +%Y%m%d%H%M 2>/dev/null || date -d "9 days ago" +%Y%m%d%H%M)
touch -t "$nine_days_marker_ts" "$COLLECTOR_BASE/.sync-r2.last-ok"
export LS_ALL="2026-09-01 00:00:00 112 rt/1/20260901.tar.gz
2026-09-01 00:00:00 222 static/8/gtfs_static_20260901.zip"
: > "$AWS_LOG"; : > "$RM_LOG"
set +e
SYNC_R2_MAX_STALE_DAYS=010 ../bin/reconcile-r2.sh --execute > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "SYNC_R2_MAX_STALE_DAYS=010 should be read as decimal 10 (a 9-day-old marker is fresh), got $rc: $(cat "$TEST_BASE/out.log")"
pass "a leading-zero SYNC_R2_MAX_STALE_DAYS (010) is read as decimal 10, not octal 8"

# --- overlap guard -----------------------------------------------------------
# A concurrent holder of the lock file must make this run skip immediately
# (exit 0, no aws calls at all), matching sync-r2.sh's own overlap guard.
# Skips cleanly (not a failure) on a platform without `flock` (e.g. macOS).
if command -v flock >/dev/null 2>&1; then
    seed_listing
    LOCK_FILE="$COLLECTOR_BASE/reconcile-r2.lock"
    HELD_MARKER="$TEST_BASE/lock-held"
    rm -f "$HELD_MARKER"
    (
        exec 9>"$LOCK_FILE"
        flock 9
        touch "$HELD_MARKER"
        sleep 2
    ) &
    holder_pid=$!

    acquired=0
    for _ in $(seq 1 100); do
        if [ -f "$HELD_MARKER" ]; then
            acquired=1
            break
        fi
        sleep 0.05
    done
    [ "$acquired" -eq 1 ] || fail "background lock holder never acquired the lock (test setup issue)"

    : > "$AWS_LOG"; : > "$RM_LOG"
    set +e
    out=$(../bin/reconcile-r2.sh 2>&1)
    code=$?
    set -e
    wait "$holder_pid"

    [ "$code" -eq 0 ] || fail "a concurrent run should skip with exit 0, got $code"
    [ -s "$AWS_LOG" ] && fail "aws was invoked despite another run holding the lock"
    echo "$out" | grep -q "already in progress" \
        || fail "skip message missing when lock was already held"
    pass "a concurrent run skips cleanly while the lock is held"
else
    echo "SKIP: flock not installed on this platform, overlap-guard behavior not exercised"
fi
