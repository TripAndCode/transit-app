#!/usr/bin/env bash
# publish-status.sh: POSTs status-snapshot.sh's last document to GitHub as a
# repository_dispatch event, over HTTPS with a bearer token -- never SSH, and
# leaving the token unset is a supported, silent no-op so this can never
# break the rest of the collector's cron behavior.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

OUT="$COLLECTOR_BASE/.status/oracle-crawler-status.json"
mkdir -p "$(dirname "$OUT")"
printf '{"schema_version":1,"component":"oracle_crawler","state":"healthy","observed_at":"2026-09-11T00:00:00Z","last_success_at":"2026-09-10T23:59:00Z","age_seconds":60,"details":{}}' > "$OUT"

run_publish() {
    : > "$CURL_LOG"
    set +e
    ../bin/publish-status.sh > "$TEST_BASE/out.log" 2>&1
    rc=$?
    set -e
}

# No token configured: a supported, silent no-op -- exits 0, never calls curl.
unset ORACLE_STATUS_GH_TOKEN
run_publish
[ "$rc" -eq 0 ] || fail "an unconfigured token should exit 0, got $rc: $(cat "$TEST_BASE/out.log")"
[ -s "$CURL_LOG" ] && fail "curl was invoked despite ORACLE_STATUS_GH_TOKEN being unset"
grep -q "disabled, skipping" "$TEST_BASE/out.log" || fail "no explanation logged for the disabled channel"
pass "an unconfigured token is a silent no-op that never calls curl"

# A configured token publishes via a POST to the dispatches endpoint,
# carrying the document as client_payload, authenticated with the token --
# never an SSH key.
export ORACLE_STATUS_GH_TOKEN="ghp_testtoken"
run_publish
[ "$rc" -eq 0 ] || fail "a successful publish should exit 0: $(cat "$TEST_BASE/out.log")"
grep -q "TripAndCode/transit-app/dispatches" "$CURL_LOG" || fail "curl did not target the dispatches endpoint"
grep -q "oracle-heartbeat" "$CURL_LOG" || fail "the event_type was not sent"
grep -q "oracle_crawler" "$CURL_LOG" || fail "the status document body was not sent"
pass "a configured token publishes the document to the dispatches endpoint"

# The bearer token itself must never be echoed or logged anywhere, even on
# success -- alert-lib.sh's own convention for its check URL.
grep -q "ghp_testtoken" "$TEST_BASE/out.log" && fail "the bearer token leaked into this script's own output"
pass "the bearer token is never echoed in this script's own output"

# A custom repo/event type is honored.
export ORACLE_STATUS_GH_REPO="someone/fork"
export ORACLE_STATUS_EVENT_TYPE="custom-event"
run_publish
grep -q "someone/fork/dispatches" "$CURL_LOG" || fail "a custom ORACLE_STATUS_GH_REPO was not used"
grep -q "custom-event" "$CURL_LOG" || fail "a custom ORACLE_STATUS_EVENT_TYPE was not used"
unset ORACLE_STATUS_GH_REPO ORACLE_STATUS_EVENT_TYPE
pass "a custom repo and event type are honored"

# No status document yet (status-snapshot.sh has never run): fails, but this
# is isolated to this one cron job and must not resemble a channel outage.
rm -f "$OUT"
run_publish
[ "$rc" -eq 1 ] || fail "a missing status document should exit 1, got $rc"
[ -s "$CURL_LOG" ] && fail "curl was invoked despite there being nothing to publish"
grep -q "nothing to publish" "$TEST_BASE/out.log" || fail "no explanation logged for the missing document"
pass "a missing status document fails without ever calling curl"

# An empty status document (e.g. a torn write somehow survived) also fails
# closed rather than publishing zero bytes as if it were real data.
printf '' > "$OUT"
run_publish
[ "$rc" -eq 1 ] || fail "an empty status document should exit 1, got $rc"
[ -s "$CURL_LOG" ] && fail "curl was invoked with an empty document"
pass "an empty status document fails without publishing anything"

# The channel being unreachable (network failure, revoked token, GitHub down)
# is reported without leaking the token, and this script's own failure stays
# isolated to itself.
printf '{"schema_version":1,"component":"oracle_crawler","state":"healthy","observed_at":"2026-09-11T00:00:00Z","last_success_at":"2026-09-10T23:59:00Z","age_seconds":60,"details":{}}' > "$OUT"
export ORACLE_STATUS_GH_TOKEN="ghp_testtoken"
export CURL_FAIL=1
run_publish
[ "$rc" -eq 1 ] || fail "an undeliverable publish should exit 1, got $rc"
grep -q "FAILED to publish" "$TEST_BASE/out.log" || fail "undelivered publish not reported"
grep -q "ghp_testtoken" "$TEST_BASE/out.log" && fail "the bearer token leaked into the failure output"
unset CURL_FAIL
pass "an unreachable channel is reported without leaking the token"
