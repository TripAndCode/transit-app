#!/usr/bin/env bash
# Keeps local dev's GTFS-RT data fresh by re-running the existing
# `ingest_live` fetch-and-ingest pass on a fixed interval. `ingest_live`
# already fetches every configured agency's feed_url and ingests in one
# call — the same logic the app's own manual refresh endpoint uses — so
# this loop adds no new ingest behavior, only repetition.
#
# On a failed ingest_live run, the interval doubles (capped) instead of
# retrying at the same pace, so a down/rate-limiting feed doesn't get
# hammered every 30s; it resets to the base interval on the next success.
# Gives up after too many consecutive failures rather than looping forever
# against a feed that is never coming back this session.
#
# Wired into `make serve` (see Makefile); not part of the production path,
# which streams from the Oracle collector or the daily Railway ingest job
# instead (see docs/deploy-railway.md).
#
# Usage: scripts/dev/local_rt_poller.sh
#   LOCAL_RT_POLL_INTERVAL_SEC      base interval between polls, seconds (default: 30)
#   LOCAL_RT_POLL_MAX_INTERVAL_SEC  backoff cap, seconds (default: 300)
#   LOCAL_RT_POLL_MAX_FAILURES      consecutive failures before giving up (default: 10)
set -uo pipefail
case "${1:-}" in -h|--help) sed -n '2,/^set /{/^set /!p;}' "$0" | sed 's/^# \{0,1\}//'; exit 0;; esac

trap 'exit 0' TERM INT

base_interval="${LOCAL_RT_POLL_INTERVAL_SEC:-30}"
max_interval="${LOCAL_RT_POLL_MAX_INTERVAL_SEC:-300}"
max_failures="${LOCAL_RT_POLL_MAX_FAILURES:-10}"

interval="$base_interval"
consecutive_failures=0

while true; do
    if poetry run python gtfs_pipeline.py ingest_live; then
        interval="$base_interval"
        consecutive_failures=0
    else
        consecutive_failures=$((consecutive_failures + 1))
        echo "local_rt_poller: ingest_live failed (${consecutive_failures}/${max_failures} consecutive failures)" >&2
        if [ "$consecutive_failures" -ge "$max_failures" ]; then
            echo "local_rt_poller: giving up after ${max_failures} consecutive failures" >&2
            exit 1
        fi
        interval=$((interval * 2))
        if [ "$interval" -gt "$max_interval" ]; then
            interval="$max_interval"
        fi
    fi
    sleep "$interval"
done
