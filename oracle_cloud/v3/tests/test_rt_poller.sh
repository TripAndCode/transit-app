#!/usr/bin/env bash
# rt-poller.sh: writes pb into UTC day dir; pings healthcheck; survives feed failure.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

printf '1\taomori\t1\thttp://feed.test/tu.pb\thttp://feed.test/static.zip\thttp://ping.test/hc\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"

# Run poller for ~2.5s (interval=1) then kill.
../bin/rt-poller.sh 1 > "$COLLECTOR_BASE/poller.out" 2>&1 &
PID=$!
sleep 2.5
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true

day=$(date -u +%Y%m%d)
count=$(ls "$COLLECTOR_BASE/data/1/rt/$day/"TripUpdate_*.pb 2>/dev/null | wc -l | tr -d ' ')
[ "$count" -ge 2 ] || fail "expected >=2 pb files, got $count"
grep -q "PBDATA" "$COLLECTOR_BASE/data/1/rt/$day/"TripUpdate_*.pb || fail "pb content missing"
ls "$COLLECTOR_BASE/data/1/rt/$day/"*.part 2>/dev/null && fail "leftover .part file"
grep -q "http://ping.test/hc" "$CURL_LOG" || fail "healthcheck ping never sent"
pass "rt-poller writes pb + pings"

# Failure mode: CURL_FAIL — poller must not crash, must not leave .part.
teardown_base
setup_base
printf '1\taomori\t1\thttp://feed.test/tu.pb\t\t\n' > "$COLLECTOR_BASE/etc/agencies.tsv"
CURL_FAIL=1 ../bin/rt-poller.sh 1 > "$COLLECTOR_BASE/poller.out" 2>&1 &
PID=$!
sleep 2
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true
ls "$COLLECTOR_BASE"/data/1/rt/*/*.part 2>/dev/null && fail ".part left on failure"
ls "$COLLECTOR_BASE"/data/1/rt/*/*.pb 2>/dev/null && fail "pb written despite failure"
pass "rt-poller failure path clean"
