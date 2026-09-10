#!/usr/bin/env bash
# health-check.sh: pings the all-clear only when every configured agency is
# still producing non-empty RT, static fetching still succeeds, and the R2
# mirror is fresh; pings /fail with the specific reason otherwise.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

day=$(date -u +%Y%m%d)
# Portable "N ago" timestamps for touch -t (BSD date first, then GNU).
old_ts=$(date -v-400d +%Y%m%d%H%M 2>/dev/null || date -d "400 days ago" +%Y%m%d%H%M)
recent_ts=$(date -v-5M +%Y%m%d%H%M 2>/dev/null || date -d "5 minutes ago" +%Y%m%d%H%M)

# Agency 1 has no static_url (its static GTFS is collected off-VM); agency 8
# has one. Both must be checked for RT, only agency 8 for static.
seed_healthy() {
    rm -rf "$COLLECTOR_BASE/data"
    rm -f "$COLLECTOR_BASE/.sync-r2.last-ok"
    printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' \
        > "$COLLECTOR_BASE/etc/agencies.tsv"
    printf '1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
        >> "$COLLECTOR_BASE/etc/agencies.tsv"
    printf '8\thiroden\t60\thttp://feed.test/tu8.pb\thttp://feed.test/s8.zip\thttp://ping.test/8\n' \
        >> "$COLLECTOR_BASE/etc/agencies.tsv"
    for id in 1 8; do
        mkdir -p "$COLLECTOR_BASE/data/$id/rt/$day"
        printf 'PBDATA-fresh' > "$COLLECTOR_BASE/data/$id/rt/$day/TripUpdate_010203.pb"
    done
    sdir="$COLLECTOR_BASE/data/8/static"
    mkdir -p "$sdir"
    head -c 4096 /dev/zero > "$sdir/gtfs_static_$day.zip"
    ln -sfn "gtfs_static_$day.zip" "$sdir/latest.zip"
    date -u > "$sdir/.static-last-ok"
    date -u > "$COLLECTOR_BASE/.sync-r2.last-ok"
}

export ALERT_PING_URL="http://alert.test/hc"
rc=0
run_hc() {
    : > "$CURL_LOG"
    set +e
    ../bin/health-check.sh > "$TEST_BASE/hc.out" 2>&1
    rc=$?
    set -e
}
pinged_ok() { grep -qE 'http://alert\.test/hc$' "$CURL_LOG"; }
pinged_fail() { grep -qE 'http://alert\.test/hc/fail$' "$CURL_LOG"; }

# Healthy collector: all-clear ping, exit 0, no failure ping.
seed_healthy
run_hc
[ "$rc" -eq 0 ] || fail "healthy collector exited $rc: $(cat "$TEST_BASE/hc.out")"
pinged_ok || fail "no all-clear ping sent for a healthy collector"
pinged_fail && fail "failure ping sent for a healthy collector"
grep -q "OK — 2 agencies" "$TEST_BASE/hc.out" || fail "healthy summary missing"
pass "healthy collector pings the all-clear and exits 0"

# An RT sample a few minutes old is normal, not an alert (interval 30 x 20).
seed_healthy
touch -t "$recent_ts" "$COLLECTOR_BASE/data/1/rt/$day/TripUpdate_010203.pb"
run_hc
[ "$rc" -eq 0 ] || fail "a 5-minute-old RT sample must not alert: $(cat "$TEST_BASE/hc.out")"
pass "a recent-but-not-current RT sample stays healthy"

# Agency 1's poller stopped: its newest sample is far past interval x factor.
seed_healthy
touch -t "$old_ts" "$COLLECTOR_BASE/data/1/rt/$day/TripUpdate_010203.pb"
run_hc
[ "$rc" -eq 1 ] || fail "stale RT should exit 1, got $rc"
pinged_fail || fail "stale RT did not ping /fail"
grep -q "newest RT sample is" "$CURL_LOG" || fail "alert body missing the stale-RT reason"
grep -q "a1 (aomori)" "$CURL_LOG" || fail "alert body does not name the affected agency"
grep -q "a8 (hiroden)" "$CURL_LOG" && fail "healthy agency 8 was reported as a problem"
pass "an agency that stopped producing RT alerts, naming that agency only"

# Unexpectedly empty output: the feed answers, with nothing in it.
seed_healthy
: > "$COLLECTOR_BASE/data/8/rt/$day/TripUpdate_010203.pb"
run_hc
[ "$rc" -eq 1 ] || fail "zero-byte RT sample should exit 1, got $rc"
grep -q "is 0 bytes" "$CURL_LOG" || fail "alert body missing the empty-sample reason"
pass "a zero-byte RT sample alerts"

# A configured agency whose poller was never enabled has no data dir at all.
seed_healthy
rm -rf "$COLLECTOR_BASE/data/8"
run_hc
[ "$rc" -eq 1 ] || fail "a configured agency with no data should exit 1, got $rc"
grep -q "no RT directory" "$CURL_LOG" || fail "alert body missing the never-ran reason"
pass "a configured agency that never produced anything alerts"

# Day dir exists but is empty: the poller died and rotate-day took the rest.
seed_healthy
rm -f "$COLLECTOR_BASE/data/1/rt/$day"/*.pb
run_hc
[ "$rc" -eq 1 ] || fail "an empty RT tree should exit 1, got $rc"
grep -q "no RT samples on disk" "$CURL_LOG" || fail "alert body missing the no-samples reason"
pass "an agency whose RT tree holds no samples alerts"

# static-fetch.sh has never succeeded for an agency that has a static_url.
seed_healthy
rm -f "$COLLECTOR_BASE/data/8/static/.static-last-ok"
run_hc
[ "$rc" -eq 1 ] || fail "missing static marker should exit 1, got $rc"
grep -q "static GTFS fetch has never succeeded" "$CURL_LOG" \
    || fail "alert body missing the never-fetched-static reason"
pass "an agency whose static fetch never succeeded alerts"

# static-fetch.sh stopped working: the marker exists but is stale. latest.zip's
# own mtime is deliberately left old too -- an unchanged upstream is normal and
# must not be what decides this, which is the whole reason the marker exists.
seed_healthy
touch -t "$old_ts" "$COLLECTOR_BASE/data/8/static/gtfs_static_$day.zip"
run_hc
[ "$rc" -eq 0 ] || fail "an old-but-unchanged static zip must not alert: $(cat "$TEST_BASE/hc.out")"
touch -t "$old_ts" "$COLLECTOR_BASE/data/8/static/.static-last-ok"
run_hc
[ "$rc" -eq 1 ] || fail "stale static marker should exit 1, got $rc"
grep -q "last successful static GTFS fetch" "$CURL_LOG" \
    || fail "alert body missing the stale-static reason"
pass "static staleness follows the fetch marker, not the zip's own mtime"

# Upstream served something tiny (an error page) instead of a GTFS zip.
seed_healthy
printf 'not a zip' > "$COLLECTOR_BASE/data/8/static/gtfs_static_$day.zip"
run_hc
[ "$rc" -eq 1 ] || fail "a tiny static zip should exit 1, got $rc"
grep -q "current static GTFS is only" "$CURL_LOG" \
    || fail "alert body missing the tiny-static reason"
pass "an implausibly small static GTFS alerts"

# latest.zip pointing at a deleted target reads as "no current static GTFS".
seed_healthy
rm -f "$COLLECTOR_BASE/data/8/static/gtfs_static_$day.zip"
run_hc
[ "$rc" -eq 1 ] || fail "a dangling latest.zip should exit 1, got $rc"
grep -q "missing or dangling" "$CURL_LOG" || fail "alert body missing the dangling-link reason"
pass "a dangling latest.zip alerts"

# R2 sync has never completed a fully-successful run.
seed_healthy
rm -f "$COLLECTOR_BASE/.sync-r2.last-ok"
run_hc
[ "$rc" -eq 1 ] || fail "missing R2 marker should exit 1, got $rc"
grep -q "R2 sync has never completed" "$CURL_LOG" || fail "alert body missing the no-R2-marker reason"
pass "a missing R2 success marker alerts"

# R2 sync marker older than SYNC_R2_MAX_STALE_DAYS -- the same threshold
# prune.sh refuses to run past, so the alert cannot drift away from it.
seed_healthy
touch -t "$old_ts" "$COLLECTOR_BASE/.sync-r2.last-ok"
run_hc
[ "$rc" -eq 1 ] || fail "stale R2 marker should exit 1, got $rc"
grep -q "R2 sync last fully succeeded" "$CURL_LOG" || fail "alert body missing the stale-R2 reason"
pass "a stale R2 success marker alerts"

# No agencies configured at all: a collector collecting nothing is not healthy.
seed_healthy
printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
run_hc
[ "$rc" -eq 1 ] || fail "an empty roster should exit 1, got $rc"
grep -q "no agencies are configured" "$CURL_LOG" || fail "alert body missing the empty-roster reason"
pass "an empty agencies roster alerts instead of reporting all-clear"

# Missing roster: cannot check anything, must not report all-clear.
seed_healthy
rm -f "$COLLECTOR_BASE/etc/agencies.tsv"
run_hc
[ "$rc" -eq 64 ] || fail "a missing roster should exit 64, got $rc"
pinged_ok && fail "all-clear pinged despite having no roster to check"
pinged_fail || fail "missing roster did not ping /fail"
pass "a missing agencies roster exits 64 and never reports all-clear"

# Unconfigured alerting still detects and logs, it just cannot page.
seed_healthy
rm -f "$COLLECTOR_BASE/.sync-r2.last-ok"
saved_url="$ALERT_PING_URL"
export ALERT_PING_URL=""
run_hc
export ALERT_PING_URL="$saved_url"
[ "$rc" -eq 1 ] || fail "problems must still exit 1 without ALERT_PING_URL, got $rc"
[ -s "$CURL_LOG" ] && fail "something was pinged despite ALERT_PING_URL being unset"
grep -q "ALERT_PING_URL is unset" "$TEST_BASE/hc.out" \
    || fail "no explanation logged when alerting is unconfigured"
grep -q "R2 sync has never completed" "$TEST_BASE/hc.out" \
    || fail "the reason itself was not logged when alerting is unconfigured"
pass "with ALERT_PING_URL unset, problems are logged and still exit nonzero"

# An unreachable alert endpoint is reported without ever printing the URL --
# it is a bearer token, and cron.log is not a secret store.
seed_healthy
export CURL_FAIL=1
run_hc
[ "$rc" -eq 1 ] || fail "an undeliverable all-clear should exit 1, got $rc"
grep -q "FAILED to deliver" "$TEST_BASE/hc.out" || fail "undelivered ping not reported"
grep -q "alert.test" "$TEST_BASE/hc.out" && fail "the alert endpoint leaked into the script's own output"
unset CURL_FAIL
pass "an undeliverable ping is reported without leaking the endpoint"

# A non-numeric threshold is rejected before it reaches any comparison.
seed_healthy
set +e
RT_STALE_FACTOR=soon ../bin/health-check.sh > "$TEST_BASE/hc.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "a non-numeric RT_STALE_FACTOR should exit 64, got $rc"
pass "a non-numeric threshold is rejected with exit 64"
