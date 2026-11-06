#!/usr/bin/env bash
# vp-poller.sh: writes VehiclePositions samples into data/<id>/vp/<UTCDAY>/,
# never pushes them to the app, and refuses (exit 64, nothing created) an
# agency that etc/vehicle_positions.tsv does not configure.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

run_poller_for() {
    local secs="$1"; shift
    ../bin/vp-poller.sh "$@" > "$COLLECTOR_BASE/poller.out" 2>&1 &
    PID=$!
    sleep "$secs"
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
}

# The app's push settings are present, as they are in /etc/environment on the
# collector, so the run below proves VP samples are never sent there.
export COLLECTOR_INGEST_URL="http://app.test/internal/collector"
export COLLECTOR_INGEST_SECRET="ingest-secret"
printf '# id\tname\tinterval\turl\n' > "$COLLECTOR_BASE/etc/vehicle_positions.tsv"
printf '8\thiroden\t1\thttp://feed.test/8/vehicle_position.bin\n' \
    >> "$COLLECTOR_BASE/etc/vehicle_positions.tsv"

run_poller_for 2.5 8
day=$(date -u +%Y%m%d)
vp_day="$COLLECTOR_BASE/data/8/vp/$day"
count=$({ find "$vp_day" -name 'VehiclePosition_[0-9][0-9][0-9][0-9][0-9][0-9].pb' -type f 2>/dev/null || true; } \
    | wc -l | tr -d ' ')
[ "$count" -ge 2 ] || fail "expected >=2 VehiclePosition_HHMMSS.pb files in $vp_day, got $count: $(cat "$COLLECTOR_BASE/poller.out")"
grep -q "PBDATA" "$vp_day"/VehiclePosition_*.pb || fail "VP sample content missing"
ls "$vp_day"/*.part 2>/dev/null && fail "leftover .part file"
[ -e "$COLLECTOR_BASE/data/8/rt" ] && fail "the VP poller touched the RT directory"
grep -q "http://feed.test/8/vehicle_position.bin" "$CURL_LOG" || fail "the configured VP url was never fetched"
pass "vp-poller writes data/<id>/vp/<UTCDAY>/VehiclePosition_HHMMSS.pb"

grep -q "/updates/" "$CURL_LOG" && fail "a VP sample was pushed to the app: $(cat "$CURL_LOG")"
grep -q "X-Collector-Secret" "$CURL_LOG" && fail "the ingest secret was sent with a VP request"
pass "vp-poller never pushes to the app, even with COLLECTOR_INGEST_* set"

# An agency the VP roster does not list runs no poller and creates nothing,
# so rotate-day.sh/sync-r2.sh never see an empty vp/ directory for it.
teardown_base
setup_base
printf '8\thiroden\t1\thttp://feed.test/8/vehicle_position.bin\n' \
    > "$COLLECTOR_BASE/etc/vehicle_positions.tsv"
set +e
../bin/vp-poller.sh 9 > "$COLLECTOR_BASE/poller.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "an unlisted agency should exit 64, got $rc"
grep -q "not found" "$COLLECTOR_BASE/poller.out" || fail "missing not-found message"
[ -e "$COLLECTOR_BASE/data/9" ] && fail "an unlisted agency got a data directory"
[ -s "$CURL_LOG" ] && fail "an unlisted agency fetched something"
pass "an agency absent from vehicle_positions.tsv runs no VP poller"

# An id that only matches numerically is not the configured agency.
set +e
../bin/vp-poller.sh 08 > "$COLLECTOR_BASE/poller.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "agency id 08 should not match row 8, got $rc"
[ -e "$COLLECTOR_BASE/data/08" ] && fail "agency id 08 got a data directory"
pass "agency ids are matched as strings"

# No roster at all: the same refusal, not an awk error.
rm -f "$COLLECTOR_BASE/etc/vehicle_positions.tsv"
set +e
../bin/vp-poller.sh 8 > "$COLLECTOR_BASE/poller.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "a missing vehicle_positions.tsv should exit 64, got $rc"
[ -e "$COLLECTOR_BASE/data/8" ] && fail "a missing roster still created a data directory"
pass "a missing vehicle_positions.tsv runs no VP poller"

# Interval 0 is rejected, matching rt-poller.sh.
printf '8\thiroden\t0\thttp://feed.test/8/vehicle_position.bin\n' \
    > "$COLLECTOR_BASE/etc/vehicle_positions.tsv"
set +e
../bin/vp-poller.sh 8 > "$COLLECTOR_BASE/poller.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "interval 0 not rejected (rc=$rc)"
grep -q "invalid interval" "$COLLECTOR_BASE/poller.out" || fail "missing invalid-interval message"
pass "vp-poller rejects interval 0"

# An empty url column is a configuration error, not a fetch of "".
printf '8\thiroden\t1\t\n' > "$COLLECTOR_BASE/etc/vehicle_positions.tsv"
set +e
../bin/vp-poller.sh 8 > "$COLLECTOR_BASE/poller.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "an empty url not rejected (rc=$rc)"
pass "vp-poller rejects an empty url"

# An empty name column must not shift the url left into its place: tab is an
# IFS whitespace character, so a plain `IFS=$'\t' read` would.
printf '8\t\t1\thttp://feed.test/8/vehicle_position.bin\n' \
    > "$COLLECTOR_BASE/etc/vehicle_positions.tsv"
run_poller_for 1.5 8
grep -q "feed=http://feed.test/8/vehicle_position.bin" "$COLLECTOR_BASE/poller.out" \
    || fail "url misparsed on a row with an empty name: $(cat "$COLLECTOR_BASE/poller.out")"
pass "an empty name column does not shift the url"

# Failure mode: the feed refuses every attempt. No sample, no .part.
teardown_base
setup_base
printf '8\thiroden\t1\thttp://feed.test/8/vehicle_position.bin\n' \
    > "$COLLECTOR_BASE/etc/vehicle_positions.tsv"
CURL_FAIL=1 run_poller_for 2 8
ls "$COLLECTOR_BASE"/data/8/vp/*/*.part 2>/dev/null && fail ".part left on failure"
ls "$COLLECTOR_BASE"/data/8/vp/*/*.pb 2>/dev/null && fail "pb written despite failure"
pass "vp-poller failure path clean"

# The startup sweep removes its own stale .part and nothing else: a
# rotate-day.sh tarball still being written beside the day dirs survives.
day=$(date -u +%Y%m%d)
mkdir -p "$COLLECTOR_BASE/data/8/vp/$day"
: > "$COLLECTOR_BASE/data/8/vp/$day/VehiclePosition_000000.pb.part"
: > "$COLLECTOR_BASE/data/8/vp/20261101.tar.gz.part"
run_poller_for 1.5 8
[ -e "$COLLECTOR_BASE/data/8/vp/$day/VehiclePosition_000000.pb.part" ] \
    && fail "stale VehiclePosition .part not swept on startup"
[ -e "$COLLECTOR_BASE/data/8/vp/20261101.tar.gz.part" ] \
    || fail "the startup sweep removed rotate-day.sh's in-flight tarball"
pass "vp-poller sweeps only its own stale .part on startup"
