#!/usr/bin/env bash
# status-snapshot.sh: builds one operations-status document (schema_version 1,
# component oracle_crawler) from the same on-disk evidence health-check.sh
# and verify-r2.sh already produce, and writes it atomically. Always exits 0
# on a well-formed document -- an unhealthy *reported* state is not a script
# failure, only a genuinely malformed run (bad threshold, can't write the
# output file) is.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

OUT="$COLLECTOR_BASE/.status/oracle-crawler-status.json"
day=$(date -u +%Y%m%d)
old_ts=$(date -v-30d +%Y%m%d%H%M 2>/dev/null || date -d "30 days ago" +%Y%m%d%H%M)
old_iso=$(date -u -v-30d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "30 days ago" +%Y-%m-%dT%H:%M:%SZ)
future_ts=$(date -v+1d +%Y%m%d%H%M 2>/dev/null || date -d "1 day" +%Y%m%d%H%M)

# Agency 1 has no static_url (its static GTFS is collected off-VM, matching
# the real Aomori configuration); agency 8 has one.
seed_healthy() {
    rm -rf "$COLLECTOR_BASE/data" "$COLLECTOR_BASE/.status"
    rm -f "$COLLECTOR_BASE/.sync-r2.last-ok" "$COLLECTOR_BASE/.verify-r2.last-result" \
        "$COLLECTOR_BASE/.verify-r2.last-success"
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
    date -u +%Y-%m-%dT%H:%M:%SZ > "$sdir/.static-last-ok"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$COLLECTOR_BASE/.sync-r2.last-ok"
    printf '%s ok 1234\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$COLLECTOR_BASE/.verify-r2.last-result"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$COLLECTOR_BASE/.verify-r2.last-success"
}

run_snapshot() {
    set +e
    ../bin/status-snapshot.sh > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}

have_python3=1
command -v python3 >/dev/null 2>&1 || have_python3=0

# A fully healthy collector reports state=healthy and exits 0.
seed_healthy
run_snapshot
[ "$rc" -eq 0 ] || fail "a healthy collector should exit 0: $(cat "$TEST_BASE/out.log")"
[ -f "$OUT" ] || fail "no status document was written"
grep -q '"state":"healthy"' "$OUT" || fail "a fresh collector should report state=healthy: $(cat "$OUT")"
grep -q '"component":"oracle_crawler"' "$OUT" || fail "component field missing/wrong"
grep -q '"schema_version":1' "$OUT" || fail "schema_version field missing/wrong"
pass "a fully healthy collector reports state=healthy and exits 0"

# Missing roster: reported as unknown, not a script failure.
seed_healthy
rm -f "$COLLECTOR_BASE/etc/agencies.tsv"
run_snapshot
[ "$rc" -eq 0 ] || fail "a missing roster should still exit 0 (unknown is a valid report): $(cat "$TEST_BASE/out.log")"
grep -q '"state":"unknown"' "$OUT" || fail "a missing roster should report state=unknown: $(cat "$OUT")"
grep -q '"static_state":"unknown"' "$OUT" || fail "a missing roster should report static_state=unknown: $(cat "$OUT")"
grep -q '"last_success_at":null' "$OUT" || fail "unknown state must pair with a null last_success_at"
grep -q '"age_seconds":null' "$OUT" || fail "unknown state must pair with a null age_seconds"
pass "a missing agencies roster reports state=unknown instead of failing the script"

# A roster file that exists but has zero data rows (header only) is the same
# underlying fact as a missing roster -- no agencies configured at all -- and
# must report the same static_state, not a different one.
seed_healthy
printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' > "$COLLECTOR_BASE/etc/agencies.tsv"
run_snapshot
[ "$rc" -eq 0 ] || fail "a header-only roster should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"static_state":"unknown"' "$OUT" || \
    fail "a header-only roster should report static_state=unknown, matching a missing roster: $(cat "$OUT")"
pass "a header-only roster (zero agencies configured) reports static_state=unknown, matching a missing roster"

# An agency's RT poller went stale: overall state follows RT down to stale.
seed_healthy
touch -t "$old_ts" "$COLLECTOR_BASE/data/1/rt/$day/TripUpdate_010203.pb"
run_snapshot
[ "$rc" -eq 0 ] || fail "a stale RT sample should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"stale"' "$OUT" || fail "a 30-day-stale RT sample should push overall state to stale: $(cat "$OUT")"
pass "a stale RT poller pushes the overall state to stale"

# One agency has no RT samples on disk at all, while a *different* agency's RT
# is independently stale: the combined rt_state must report the worse verdict
# (stale), not be masked down to unknown by the agency with no RT data.
seed_healthy
rm -rf "$COLLECTOR_BASE/data/1/rt"
touch -t "$old_ts" "$COLLECTOR_BASE/data/8/rt/$day/TripUpdate_010203.pb"
run_snapshot
[ "$rc" -eq 0 ] || fail "a mixed missing/stale RT roster should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"rt_state":"stale"' "$OUT" || \
    fail "one agency with no RT data must not mask a different agency's genuine staleness: $(cat "$OUT")"
pass "one agency with no RT data does not mask a different agency's stale rt_state"

# An agency configured for static GTFS never had a successful fetch: unknown.
seed_healthy
rm -f "$COLLECTOR_BASE/data/8/static/.static-last-ok"
run_snapshot
[ "$rc" -eq 0 ] || fail "a never-succeeded static fetch should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"unknown"' "$OUT" || fail "a static fetch that never succeeded should report unknown: $(cat "$OUT")"
pass "a static fetch that never succeeded pushes the overall state to unknown"

# Two agencies are configured for static GTFS: one never had a successful
# fetch (missing marker) while the other's fetch is independently stale. The
# combined static_state must report the worse verdict (stale), not be masked
# down to unknown by the agency with no marker at all.
seed_healthy
printf '9\thakodate\t60\thttp://feed.test/tu9.pb\thttp://feed.test/s9.zip\thttp://ping.test/9\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"
mkdir -p "$COLLECTOR_BASE/data/9/rt/$day" "$COLLECTOR_BASE/data/9/static"
printf 'PBDATA-fresh' > "$COLLECTOR_BASE/data/9/rt/$day/TripUpdate_010203.pb"
date -u +%Y-%m-%dT%H:%M:%SZ > "$COLLECTOR_BASE/data/9/static/.static-last-ok"
touch -t "$old_ts" "$COLLECTOR_BASE/data/9/static/.static-last-ok"
rm -f "$COLLECTOR_BASE/data/8/static/.static-last-ok"
run_snapshot
[ "$rc" -eq 0 ] || fail "a mixed missing/stale static roster should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"static_state":"stale"' "$OUT" || \
    fail "one agency with no static marker must not mask a different agency's genuine staleness: $(cat "$OUT")"
pass "one agency with no static marker does not mask a different agency's stale static_state"

# No agency has a static_url at all: static is not_applicable and must not
# drag a healthy collector down to unknown/stale.
seed_healthy
printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n1\taomori\t30\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
rm -rf "$COLLECTOR_BASE/data/8"
run_snapshot
[ "$rc" -eq 0 ] || fail "an all-off-VM-static roster should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"healthy"' "$OUT" || fail "no agency having a static_url must not drag the state down: $(cat "$OUT")"
grep -q '"static_state":"not_applicable"' "$OUT" || fail "static should be not_applicable, not unknown/stale: $(cat "$OUT")"
pass "an all-off-VM-static roster (no static_url anywhere) reports static as not_applicable, not a problem"

# R2 sync has never completed: reported as unknown, with an explicit
# sync_result of "unknown" (never "fail" -- sync-r2.sh has no on-disk
# "attempted and failed" signal, only "has/hasn't ever recorded a success").
seed_healthy
rm -f "$COLLECTOR_BASE/.sync-r2.last-ok"
run_snapshot
[ "$rc" -eq 0 ] || fail "a missing sync marker should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"unknown"' "$OUT" || fail "a missing R2 sync marker should report unknown: $(cat "$OUT")"
grep -q '"sync_result":"unknown"' "$OUT" || fail "sync_result should be unknown, not fail, when the marker has never been written: $(cat "$OUT")"
pass "R2 sync having never completed reports state=unknown with sync_result=unknown"

# R2 sync marker present: sync_result is explicitly "ok".
seed_healthy
run_snapshot
grep -q '"sync_result":"ok"' "$OUT" || fail "a present sync marker should report sync_result=ok: $(cat "$OUT")"
pass "a present R2 sync marker reports sync_result=ok"

# R2 sync marker present but stale (30 days old, over SYNC_R2_MAX_STALE_DAYS).
seed_healthy
printf '%s\n' "$old_iso" > "$COLLECTOR_BASE/.sync-r2.last-ok"
run_snapshot
[ "$rc" -eq 0 ] || fail "a stale sync marker should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"stale"' "$OUT" || fail "a 30-day-stale R2 sync marker should report stale: $(cat "$OUT")"
pass "a stale R2 sync marker pushes the overall state to stale"

# verify-r2.sh's own last recorded result was a failure: reported as failed,
# the one state a subsystem must explicitly report rather than infer from age.
# With no genuine prior success on record (RESULT_MARKER is written on every
# completed run, success or failure, so its own timestamp here belongs to the
# failed run itself, not a success), last_success_at/age_seconds must be
# null/absent rather than that failed run's own timestamp.
seed_healthy
rm -f "$COLLECTOR_BASE/.verify-r2.last-success"
printf '%s fail 7\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$COLLECTOR_BASE/.verify-r2.last-result"
run_snapshot
[ "$rc" -eq 0 ] || fail "a failed verify-r2 result should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"failed"' "$OUT" || fail "a recorded verify-r2 failure should report state=failed: $(cat "$OUT")"
grep -q '"last_success_at":null' "$OUT" || fail "a verify-r2 failure with no prior success must report last_success_at:null, not the failed run's own timestamp: $(cat "$OUT")"
grep -q '"age_seconds":null' "$OUT" || fail "a verify-r2 failure with no prior success must report age_seconds:null: $(cat "$OUT")"
pass "verify-r2.sh's own recorded failure reports state=failed regardless of age"
pass "a verify-r2 failure with no prior success reports last_success_at:null instead of the failed run's own timestamp"

# Same failure, but with a genuine prior success on record (an older
# SUCCESS_MARKER, distinct from the fresh failed-run timestamp in
# RESULT_MARKER): last_success_at must reflect that real prior success, not
# the failed run's own timestamp.
seed_healthy
prior_success_iso=$(date -u -v-1d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "1 day ago" +%Y-%m-%dT%H:%M:%SZ)
printf '%s\n' "$prior_success_iso" > "$COLLECTOR_BASE/.verify-r2.last-success"
printf '%s fail 7\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$COLLECTOR_BASE/.verify-r2.last-result"
run_snapshot
[ "$rc" -eq 0 ] || fail "a failed verify-r2 result with a prior success should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"failed"' "$OUT" || fail "a recorded verify-r2 failure should report state=failed even with a prior success on record: $(cat "$OUT")"
grep -q "\"last_success_at\":\"$prior_success_iso\"" "$OUT" || fail "a verify-r2 failure with a genuine prior success should report that success's own timestamp, not the failed run's: $(cat "$OUT")"
pass "a verify-r2 failure with a genuine prior success reports that success's own timestamp, not the failed run's"

# verify-r2.sh has never run at all: unknown, not failed (no failure was ever
# actually reported -- this is "cannot tell", not "confirmed broken").
seed_healthy
rm -f "$COLLECTOR_BASE/.verify-r2.last-result"
run_snapshot
[ "$rc" -eq 0 ] || fail "a missing verify-r2 result should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"state":"unknown"' "$OUT" || fail "verify-r2.sh never having run should report unknown, not failed: $(cat "$OUT")"
pass "verify-r2.sh never having run reports unknown rather than failed"

# The R2 object total from verify-r2.sh's marker is carried into details.
seed_healthy
printf '%s ok 4321\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$COLLECTOR_BASE/.verify-r2.last-result"
run_snapshot
grep -q '"r2_object_total":4321' "$OUT" || fail "r2_object_total should be read from verify-r2.sh's marker: $(cat "$OUT")"
pass "the R2 object total from verify-r2.sh's marker is carried into the document"

# A leading-zero r2_object_total in the marker (hand-edited or from a
# differently-formatted writer) must normalize to a plain integer -- a
# literal leading zero is not a valid JSON number token.
seed_healthy
printf '%s ok 0042\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$COLLECTOR_BASE/.verify-r2.last-result"
run_snapshot
grep -q '"r2_object_total":42' "$OUT" || \
    fail "a leading-zero r2_object_total marker value should normalize to a plain integer: $(cat "$OUT")"
if [ "$have_python3" -eq 1 ]; then
    python3 -c "import json; json.load(open('$OUT'))" || \
        fail "a leading-zero r2_object_total must not break the document's JSON validity: $(cat "$OUT")"
fi
pass "a leading-zero r2_object_total marker value normalizes to a plain integer"

# A future-dated marker/mtime (e.g. after a backward clock step elsewhere)
# must not produce a negative age -- both the per-subsystem detail age and
# the document-level age_seconds are clamped at zero.
seed_healthy
touch -t "$future_ts" "$COLLECTOR_BASE/data/1/rt/$day/TripUpdate_010203.pb"
run_snapshot
[ "$rc" -eq 0 ] || fail "a future-dated RT sample should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"rt_worst_age_seconds":-' "$OUT" && fail "rt_worst_age_seconds must not be negative: $(cat "$OUT")"
grep -q '"age_seconds":-' "$OUT" && fail "the document-level age_seconds must not be negative: $(cat "$OUT")"
if [ "$have_python3" -eq 1 ]; then
    python3 -c "import json; json.load(open('$OUT'))" || \
        fail "a future-dated marker must not break the document's JSON validity: $(cat "$OUT")"
fi
pass "a future-dated RT sample clamps both rt_worst_age_seconds and age_seconds at zero instead of going negative"

# A leading-zero interval in agencies.tsv (e.g. "089") is a bare arithmetic
# operand -- bash treats a leading zero as octal, and "089" isn't valid
# octal at all, which previously crashed the whole script instead of
# degrading gracefully like every other malformed-input case here.
seed_healthy
printf '# id\tname\tinterval\tfeed_url\tstatic_url\tping_url\n' > "$COLLECTOR_BASE/etc/agencies.tsv"
printf '1\taomori\t089\thttp://feed.test/tu.pb\t\thttp://ping.test/1\n' >> "$COLLECTOR_BASE/etc/agencies.tsv"
run_snapshot
[ "$rc" -eq 0 ] || fail "a leading-zero interval should still exit 0, not crash: $(cat "$TEST_BASE/out.log")"
[ -f "$OUT" ] || fail "a leading-zero interval must still produce a status document"
if [ "$have_python3" -eq 1 ]; then
    python3 -c "import json; json.load(open('$OUT'))" || \
        fail "a leading-zero interval must not break the document's JSON validity: $(cat "$OUT")"
fi
pass "a leading-zero agency interval does not crash the script"

# Disk usage details are populated with plausible (non-null) numbers.
seed_healthy
run_snapshot
grep -q '"disk_used_pct":null' "$OUT" && fail "disk_used_pct should not be null on a real filesystem: $(cat "$OUT")"
grep -q '"disk_free_bytes":null' "$OUT" && fail "disk_free_bytes should not be null on a real filesystem: $(cat "$OUT")"
pass "disk usage details are populated"

# A non-numeric threshold is rejected before anything is read or written.
seed_healthy
rm -rf "$COLLECTOR_BASE/.status"
set +e
RT_STALE_FACTOR=soon ../bin/status-snapshot.sh > "$TEST_BASE/out.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 64 ] || fail "a non-numeric RT_STALE_FACTOR should exit 64, got $rc"
[ -f "$OUT" ] && fail "a rejected threshold must not still write a status document"
pass "a non-numeric threshold is rejected with exit 64 and writes nothing"

# The write is atomic: no leftover temp file survives a normal run.
seed_healthy
run_snapshot
leftover=$(find "$COLLECTOR_BASE/.status" -name 'oracle-crawler-status.json.??????' 2>/dev/null)
[ -z "$leftover" ] || fail "a temp file was left behind after an atomic write: $leftover"
pass "the status document is written atomically with no leftover temp file"

# Re-running never crashes on an already-populated output directory/file.
seed_healthy
run_snapshot
first_rc=$rc
run_snapshot
[ "$rc" -eq 0 ] && [ "$first_rc" -eq 0 ] || fail "re-running status-snapshot.sh should be idempotent"
pass "re-running status-snapshot.sh is idempotent"

if [ "$have_python3" -eq 1 ]; then
    seed_healthy
    run_snapshot
    python3 -c "import json; json.load(open('$OUT'))" || fail "the written document is not valid JSON"
    pass "the written document parses as valid JSON"
fi

# A hand-edited or corrupted .verify-r2.last-result marker (anything other
# than the literal ok/fail record_result writes) must not flow unescaped
# into the JSON document -- it is normalized to verify_result=unknown, and
# that unrecognized-result case must also force verify_state (and therefore
# r2_state) to unknown rather than falling through to classify_ratio as if
# it were a genuine "ok" against the still-fresh .verify-r2.last-success
# marker seed_healthy leaves in place.
seed_healthy
printf '%s "bogus\\n" 7\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$COLLECTOR_BASE/.verify-r2.last-result"
run_snapshot
[ "$rc" -eq 0 ] || fail "a corrupted verify_result marker should still exit 0: $(cat "$TEST_BASE/out.log")"
grep -q '"verify_result":"unknown"' "$OUT" || \
    fail "a corrupted verify_result value should be normalized to unknown: $(cat "$OUT")"
grep -q '"r2_state":"unknown"' "$OUT" || \
    fail "an unreadable verify_result must force r2_state=unknown, not a ratio-derived state off a stale success epoch: $(cat "$OUT")"
if [ "$have_python3" -eq 1 ]; then
    python3 -c "import json; json.load(open('$OUT'))" || \
        fail "a corrupted verify_result value must not break the document's JSON validity: $(cat "$OUT")"
fi
pass "a corrupted verify_result marker value is normalized to unknown and forces r2_state=unknown, instead of falling through to a ratio-derived state"
