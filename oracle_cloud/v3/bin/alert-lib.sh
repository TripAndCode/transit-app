#!/usr/bin/env bash
# Shared alert transport for the collector. Sourced by other bin/ scripts,
# never executed directly.
#
# Delivery reuses the healthchecks.io convention agencies.tsv already uses for
# the per-agency RT pollers: one check URL where a ping to the bare URL means
# "all good" and a ping to <url>/fail means "something is wrong". The reason
# text is POSTed as the ping body, so the resulting notification carries the
# actual problem instead of only "check went down".
#
# Env:
#   ALERT_PING_URL  healthchecks.io check URL for collector health. Leaving it
#                   unset is a supported configuration: alerting degrades to
#                   the pre-existing log-only behavior and the caller's exit
#                   status is unchanged, so a VM that has not been given a
#                   check URL yet still runs normally instead of failing every
#                   cron job on a missing variable.
#   ALERT_CURL      curl binary override (tests).
#
# The check URL is a bearer secret: anyone holding it can forge an all-clear.
# It is therefore never echoed, not even in this file's own error messages.

case "${0##*/}" in
    alert-lib.sh)
        echo "alert-lib.sh is a sourced helper, not a command" >&2
        exit 64
        ;;
esac

# alert_report <ok|fail> <body>
# Returns 0 when the ping was delivered (or when no endpoint is configured, so
# an unconfigured VM is never treated as a delivery failure), 1 when an
# endpoint is configured but unreachable, 2 on a bad call.
alert_report() {
    # `local` throughout: this runs inside long scripts that have their own
    # `body`/`url` variables, and silently clobbering one would be a nasty
    # failure mode for a function whose whole job is reporting failures.
    local outcome="${1:-}" body="${2:-}" alert_base alert_url
    alert_base="${ALERT_PING_URL:-}"
    alert_base="${alert_base%/}"
    case "$outcome" in
        ok) alert_url="$alert_base" ;;
        fail) alert_url="${alert_base:+$alert_base/fail}" ;;
        *)
            echo "alert_report: unknown outcome '$outcome' (want ok|fail)" >&2
            return 2
            ;;
    esac

    if [ -z "$alert_url" ]; then
        if [ "$outcome" = fail ]; then
            echo "alert: ALERT_PING_URL is unset — this failure is only being" \
                "logged, nobody is being paged:" >&2
            echo "$body" >&2
        fi
        return 0
    fi

    # --retry covers a blip on the collector VM's own uplink; a hard failure is
    # reported but must never change what the calling script concluded about
    # the collector itself.
    if ! "${ALERT_CURL:-curl}" -fsS -m 10 --retry 3 -o /dev/null \
        --data-binary "$body" "$alert_url"; then
        echo "alert: FAILED to deliver the '$outcome' ping to ALERT_PING_URL" >&2
        return 1
    fi
    return 0
}
