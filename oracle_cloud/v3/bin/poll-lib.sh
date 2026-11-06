#!/usr/bin/env bash
# Shared fetch step for the GTFS-RT pollers (rt-poller.sh, vp-poller.sh).
# Sourced by them, never executed directly.

case "${0##*/}" in
    poll-lib.sh)
        echo "poll-lib.sh is a sourced helper, not a command" >&2
        exit 64
        ;;
esac

# fetch_with_retry <url> <out>
# Downloads <url> into <out>, making up to 4 attempts and sleeping 2, 4, 8 and
# 16 seconds after each failed one. Returns 1 once every attempt has failed.
# <out> is the caller's `.part` name: the caller renames it into place on
# success and removes it on failure, so a reader never sees a partial sample.
# Call it directly or as an `if` condition, never in `$(...)` or a pipeline:
# the pollers' TERM/EXIT traps clean up the in-flight `.part`, and they only
# run in the main shell.
fetch_with_retry() {
    local url="$1" out="$2" attempt=1 backoff=2
    while [ "$attempt" -le 4 ]; do
        if curl -sf --max-time 8 --output "$out" "$url"; then
            return 0
        fi
        sleep "$backoff"
        backoff=$(( backoff * 2 ))
        attempt=$(( attempt + 1 ))
    done
    return 1
}
