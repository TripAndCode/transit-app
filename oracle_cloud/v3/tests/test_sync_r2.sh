#!/usr/bin/env bash
# sync-r2.sh: syncs each agency's rt/static dir to the right R2 path, fails
# closed when required OBJECT_STORE_* env vars are missing, and isolates
# one agency's sync failure from the rest of the run.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

export AWS_LOG="$TEST_BASE/aws.log"
# Fake aws: records every invocation; `s3 sync` is a no-op otherwise.
# AWS_FAIL_MATCH: if set and any arg contains this substring, exit 1
# instead of 0 (simulates one agency's sync failing).
cat > "$SHIM_DIR/aws" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$AWS_LOG"
if [ -n "${AWS_FAIL_MATCH:-}" ]; then
    for a in "$@"; do
        case "$a" in *"$AWS_FAIL_MATCH"*) exit 1 ;; esac
    done
fi
exit 0
SHIM
chmod +x "$SHIM_DIR/aws"

mkdir -p "$COLLECTOR_BASE/data/1/rt" "$COLLECTOR_BASE/data/1/static"
mkdir -p "$COLLECTOR_BASE/data/8/static"

# Missing required env -> fails closed, no aws calls made.
if OBJECT_STORE_ENDPOINT= OBJECT_STORE_BUCKET= OBJECT_STORE_ACCESS_KEY_ID= OBJECT_STORE_SECRET_ACCESS_KEY= \
    ../bin/sync-r2.sh 2>/dev/null; then
    fail "sync-r2.sh should fail when OBJECT_STORE_* is unset"
fi
[ -s "$AWS_LOG" ] && fail "aws was invoked despite missing required env"
pass "missing OBJECT_STORE_* fails closed before calling aws"

# With env set: syncs both agencies' rt and/or static dirs to the matching
# per-agency R2 paths, scoped to the right file patterns and endpoint.
export OBJECT_STORE_ENDPOINT="https://example.r2.cloudflarestorage.com"
export OBJECT_STORE_BUCKET="test-bucket"
export OBJECT_STORE_ACCESS_KEY_ID="AKIDTEST"
export OBJECT_STORE_SECRET_ACCESS_KEY="secrettest"
../bin/sync-r2.sh >/dev/null

grep -q "s3 sync $COLLECTOR_BASE/data/1/rt s3://test-bucket/rt/1" "$AWS_LOG" \
    || fail "agency 1 rt not synced to rt/1"
grep -q "s3 sync $COLLECTOR_BASE/data/1/static s3://test-bucket/static/1" "$AWS_LOG" \
    || fail "agency 1 static not synced to static/1"
grep -q "s3 sync $COLLECTOR_BASE/data/8/static s3://test-bucket/static/8" "$AWS_LOG" \
    || fail "agency 8 static not synced to static/8"
grep -q -- "--include \*.tar.gz" "$AWS_LOG" || fail "rt sync missing tar.gz include filter"
grep -q -- "--include gtfs_static_\*.zip" "$AWS_LOG" || fail "static sync missing zip include filter"
grep -q -- "--endpoint-url https://example.r2.cloudflarestorage.com" "$AWS_LOG" \
    || fail "sync calls missing --endpoint-url (would silently hit real AWS S3 instead of R2)"
pass "each agency's rt/static synced to its own R2 path with the right filters and endpoint"

# Credentials are never passed as CLI args (only via env) -- grep the log,
# not just trust the script text, so a future edit that leaks a --secret-key
# flag here would actually be caught.
grep -q "secrettest" "$AWS_LOG" && fail "secret leaked onto the aws command line"
pass "secret never appears on the aws command line"

# A fully-successful run writes the marker prune.sh depends on.
OK_MARKER="$COLLECTOR_BASE/.sync-r2.last-ok"
[ -f "$OK_MARKER" ] || fail "success marker not written after a fully-successful run"
pass "success marker written after a fully-successful run"

# One agency's sync failing must not abort the rest of the run, and the
# script must still exit nonzero overall so cron/monitoring can see it.
marker_before=$(cat "$OK_MARKER")
: > "$AWS_LOG"
if AWS_FAIL_MATCH="rt/1" ../bin/sync-r2.sh >/dev/null 2>&1; then
    fail "sync-r2.sh should exit nonzero when any agency's sync failed"
fi
grep -q "s3 sync $COLLECTOR_BASE/data/8/static s3://test-bucket/static/8" "$AWS_LOG" \
    || fail "agency 8 was skipped after agency 1's rt sync failed"
pass "one agency's sync failure doesn't skip the rest, but still exits nonzero"

# A partial failure must NOT refresh the marker -- otherwise prune.sh would
# treat a permanently-failing agency as "safe" forever because every other
# agency keeps succeeding.
[ "$(cat "$OK_MARKER")" = "$marker_before" ] \
    || fail "marker was refreshed despite a partial failure"
pass "a partial failure does not refresh the success marker"

# Overlap guard: a concurrent holder of the lock file must make this run
# skip immediately (exit 0, no aws calls), not race it. Every test above
# already ran on whatever platform this suite executes on -- on a box
# without `flock` (e.g. macOS), the earlier "sync-r2: WARNING flock not
# found" message on stderr is the degrade-gracefully path's own coverage.
# This test only exercises the locking behavior itself, so it skips
# cleanly (not a failure) where `flock` isn't installed.
if command -v flock >/dev/null 2>&1; then
    LOCK_FILE="$COLLECTOR_BASE/sync-r2.lock"
    HELD_MARKER="$TEST_BASE/lock-held"
    (
        exec 9>"$LOCK_FILE"
        flock 9
        touch "$HELD_MARKER"
        sleep 2
    ) &
    holder_pid=$!

    # Poll for the background subshell to actually acquire the lock, rather
    # than assume a fixed sleep is long enough (avoids flaking under a
    # slow/loaded runner).
    acquired=0
    for _ in $(seq 1 100); do
        if [ -f "$HELD_MARKER" ]; then
            acquired=1
            break
        fi
        sleep 0.05
    done
    [ "$acquired" -eq 1 ] || fail "background lock holder never acquired the lock (test setup issue)"

    : > "$AWS_LOG"
    if out=$(../bin/sync-r2.sh 2>&1); then
        code=0
    else
        code=$?
    fi
    wait "$holder_pid"

    [ "$code" -eq 0 ] || fail "a concurrent run should skip with exit 0, got $code"
    [ -s "$AWS_LOG" ] && fail "aws was invoked despite another run holding the lock"
    echo "$out" | grep -q "already in progress" \
        || fail "skip message missing when lock was already held"
    pass "a concurrent run skips cleanly while the lock is held"
else
    echo "SKIP: flock not installed on this platform, overlap-guard behavior not exercised"
fi

# Vacuous "success" guard: an empty/misconfigured COLLECTOR_BASE (no
# data/*/rt or data/*/static dirs at all -- e.g. a typo'd env var) must
# NOT write the marker or exit 0. Otherwise prune.sh would see a fresh
# marker and delete local data that was never even looked at, let alone
# mirrored.
EMPTY_BASE=$(mktemp -d)
mkdir -p "$EMPTY_BASE/data" # data/ exists, but has no per-agency subdirs
: > "$AWS_LOG"
if COLLECTOR_BASE="$EMPTY_BASE" ../bin/sync-r2.sh >/dev/null 2>&1; then
    fail "sync-r2.sh should exit nonzero when no agency directories exist"
fi
[ -s "$AWS_LOG" ] && fail "aws was invoked despite no agency directories existing"
[ -f "$EMPTY_BASE/.sync-r2.last-ok" ] \
    && fail "success marker was written despite processing zero agencies"
rm -rf "$EMPTY_BASE"
pass "an empty COLLECTOR_BASE exits nonzero and never writes the success marker"
