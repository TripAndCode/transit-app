#!/usr/bin/env bash
# Run one collector cron job and alert when it fails.
#
# Usage (from crontab): cron-wrap.sh /path/to/job.sh [args...]
#
# cron already appends each job's output to cron.log; what it does not do is
# tell anyone that a job exited nonzero. This wrapper keeps the logging exactly
# as it was (the job's combined output is echoed straight through to whatever
# the crontab line redirects into) and adds the missing half: a failure ping
# carrying the job name, its exit status, and the tail of what it printed, so
# an expired R2 credential or an upstream static feed gone 404 reaches a human
# the same day instead of on the day someone next reads cron.log.
#
# Deliberately failure-only: health-check.sh owns the periodic all-clear, and a
# per-job success ping here would keep re-clearing a check whose underlying
# condition is still broken.
#
# Env: ALERT_PING_URL (see alert-lib.sh); CRON_ALERT_PING_URL to route job
# failures to a different check than the periodic health check;
# CRON_WRAP_TAIL_LINES (default 20) for how much output to attach.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=alert-lib.sh
. "$SCRIPT_DIR/alert-lib.sh"

ALERT_PING_URL="${CRON_ALERT_PING_URL:-${ALERT_PING_URL:-}}"
TAIL_LINES="${CRON_WRAP_TAIL_LINES-20}"
case "$TAIL_LINES" in
    ''|*[!0-9]*)
        echo "cron-wrap: CRON_WRAP_TAIL_LINES must be a non-negative integer" >&2
        exit 64
        ;;
esac

[ "$#" -ge 1 ] || { echo "usage: cron-wrap.sh <job> [args...]" >&2; exit 64; }
job=$(basename -- "$1")

out=$(mktemp "${TMPDIR:-/tmp}/cron-wrap.XXXXXX") || {
    # No scratch file means no captured output to attach — run the job
    # unwrapped rather than not running it at all. A collector that stops
    # collecting because its alerting plumbing broke is strictly worse than
    # one that collects unwatched for a day.
    echo "cron-wrap: could not create a temp file, running $job unwrapped" >&2
    exec "$@"
}
trap 'rm -f "$out"' EXIT INT TERM

# stdout and stderr are merged, since the crontab lines send both to the same
# cron.log anyway and the alert body wants them interleaved in real order.
"$@" > "$out" 2>&1
rc=$?

cat "$out"

[ "$rc" -eq 0 ] && exit 0

echo "cron-wrap: $job exited $rc" >&2
alert_report fail "$(printf '%s failed (exit %s) on %s\n\n%s' \
    "$job" "$rc" "$(hostname 2>/dev/null || echo unknown-host)" \
    "$(tail -n "$TAIL_LINES" "$out")")"

# Propagate the job's own status: cron.log, and any human reading it, should
# still see exactly what the job returned.
exit "$rc"
