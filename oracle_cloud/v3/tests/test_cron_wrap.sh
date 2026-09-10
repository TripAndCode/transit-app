#!/usr/bin/env bash
# cron-wrap.sh: passes a job's output through to cron.log unchanged, forwards
# its exit status, and pings /fail with the reason only when it failed.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

export ALERT_PING_URL="http://alert.test/hc"
JOB="$TEST_BASE/bin/job.sh"
cat > "$JOB" <<'JOB'
#!/usr/bin/env bash
echo "line one"
echo "line two: args=$*"
echo "line three (stderr)" >&2
exit "${JOB_EXIT:-0}"
JOB
chmod +x "$JOB"

rc=0
run_wrap() {
    : > "$CURL_LOG"
    set +e
    ../bin/cron-wrap.sh "$@" > "$TEST_BASE/wrap.out" 2>&1
    rc=$?
    set -e
}
pinged_fail() { grep -qE 'http://alert\.test/hc/fail$' "$CURL_LOG"; }

# Successful job: output still reaches cron.log, nothing is pinged.
run_wrap "$JOB" alpha beta
[ "$rc" -eq 0 ] || fail "a successful job should exit 0, got $rc"
grep -q "line one" "$TEST_BASE/wrap.out" || fail "job stdout was swallowed"
grep -q "line three (stderr)" "$TEST_BASE/wrap.out" || fail "job stderr was swallowed"
grep -q "args=alpha beta" "$TEST_BASE/wrap.out" || fail "job arguments were not forwarded"
[ -s "$CURL_LOG" ] && fail "a successful job pinged the alert endpoint"
pass "a successful job logs as before and pings nothing"

# Failing job: same output, same exit status, plus a failure ping carrying the
# job name, its status, and what it printed.
export JOB_EXIT=3
run_wrap "$JOB"
[ "$rc" -eq 3 ] || fail "the job's own exit status should be forwarded, got $rc"
grep -q "line one" "$TEST_BASE/wrap.out" || fail "job output missing on the failure path"
grep -q "job.sh exited 3" "$TEST_BASE/wrap.out" || fail "no failure line written to the log"
pinged_fail || fail "a failing job did not ping /fail"
grep -q "job.sh failed (exit 3)" "$CURL_LOG" || fail "alert body missing the job name/status"
grep -q "line three (stderr)" "$CURL_LOG" || fail "alert body missing the job's output tail"
pass "a failing job forwards its status and pings /fail with the reason"

# The attached tail is bounded: a job that printed a flood must not be POSTed
# in full to the alert endpoint.
export JOB_EXIT=1 CRON_WRAP_TAIL_LINES=1
run_wrap "$JOB"
[ "$rc" -eq 1 ] || fail "expected exit 1, got $rc"
grep -q "line three (stderr)" "$CURL_LOG" || fail "the last output line should always be attached"
grep -q "line one" "$CURL_LOG" && fail "CRON_WRAP_TAIL_LINES did not bound the attached output"
pass "the attached output tail respects CRON_WRAP_TAIL_LINES"

# Failures can be routed to a check of their own.
unset CRON_WRAP_TAIL_LINES
export CRON_ALERT_PING_URL="http://other.test/jobs"
run_wrap "$JOB"
unset CRON_ALERT_PING_URL
grep -qE 'http://other\.test/jobs/fail$' "$CURL_LOG" \
    || fail "CRON_ALERT_PING_URL was not used for the job failure"
grep -q "alert.test" "$CURL_LOG" && fail "the health-check endpoint was pinged too"
pass "CRON_ALERT_PING_URL routes job failures to their own check"

# Unconfigured alerting: the job still runs, still logs, still forwards status.
saved_url="$ALERT_PING_URL"
export ALERT_PING_URL=""
export JOB_EXIT=4
run_wrap "$JOB"
export ALERT_PING_URL="$saved_url"
[ "$rc" -eq 4 ] || fail "exit status must be forwarded without ALERT_PING_URL, got $rc"
[ -s "$CURL_LOG" ] && fail "something was pinged despite ALERT_PING_URL being unset"
grep -q "ALERT_PING_URL is unset" "$TEST_BASE/wrap.out" \
    || fail "no explanation logged when alerting is unconfigured"
pass "without ALERT_PING_URL the job still runs and its failure is logged"

# An undeliverable ping must not change what the job returned.
export CURL_FAIL=1
export JOB_EXIT=5
run_wrap "$JOB"
unset CURL_FAIL
[ "$rc" -eq 5 ] || fail "an undeliverable ping changed the job's exit status ($rc)"
grep -q "FAILED to deliver" "$TEST_BASE/wrap.out" || fail "undelivered ping not reported"
grep -q "alert.test" "$TEST_BASE/wrap.out" && fail "the alert endpoint leaked into the log"
pass "an undeliverable ping is reported without changing the job's status"

# Called with no job at all.
run_wrap
[ "$rc" -eq 64 ] || fail "no arguments should exit 64, got $rc"
pass "cron-wrap.sh rejects being called with no job"
