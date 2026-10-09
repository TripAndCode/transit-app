#!/usr/bin/env bash
# deploy/vps/run-with-ping.sh NAME COMMAND...
# Runs COMMAND and reports it to healthchecks.io: /start before, the bare URL
# on success, /fail with the output's tail on failure. HC_PING_URL_<NAME>
# unset runs the job unreported. COMMAND's exit status is the script's.
set -uo pipefail
name="$1"
shift
url_var="HC_PING_URL_${name}"
url="${!url_var:-}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

ping_hc() {
  [ -n "$url" ] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "$@" || true
}

ping_hc "$url/start"
"$@" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
if [ "$status" -eq 0 ]; then
  ping_hc "$url"
else
  ping_hc --data-raw "$(tail -c 10000 "$log")" "$url/fail"
fi
exit "$status"
