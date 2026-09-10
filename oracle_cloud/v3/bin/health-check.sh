#!/usr/bin/env bash
# Collector health check — the one script that actively pages a human.
#
# Every other script in bin/ reports trouble by writing to stderr, which cron
# appends to cron.log, where it sits unread until someone goes looking. This
# script instead re-derives, from what is actually on disk right now, whether
# each configured agency is still producing data and whether R2 still holds a
# confirmed-fresh mirror of it, and pings the alert endpoint with the specific
# reasons when it does not. Running it on a short cron interval also means the
# check itself going silent (VM down, cron broken) trips the endpoint's own
# period/grace alarm — a failure mode no in-script check can ever catch.
#
# Per configured agency in etc/agencies.tsv:
#   * RT still flowing   — the newest TripUpdate_*.pb sample is younger than
#                          interval * RT_STALE_FACTOR. Covers a poller that
#                          died, a unit that was never enabled for an agency
#                          that IS configured, and a feed_url that started
#                          refusing us: all three simply stop producing files.
#   * RT not empty       — that sample is at least MIN_RT_BYTES.
#   * static still fetched — static-fetch.sh's per-agency .static-last-ok
#                          marker is younger than STATIC_MAX_STALE_DAYS. The
#                          marker exists because latest.zip is rewritten only
#                          when upstream *content* changes, so its own mtime
#                          cannot distinguish "upstream is stable" (fine) from
#                          "we stopped fetching" (not fine).
#   * static not empty   — the latest.zip target is at least MIN_STATIC_BYTES;
#                          an upstream error page served as 200 is tiny, and
#                          curl -f cannot see that it is not a GTFS zip.
# Once globally:
#   * R2 mirror fresh    — sync-r2.sh's .sync-r2.last-ok marker exists and is
#                          younger than SYNC_R2_MAX_STALE_DAYS. Deliberately
#                          the same variable prune.sh gates its refusal on, so
#                          "nobody was told" and "pruning has stopped" can
#                          never drift apart into two different thresholds.
#
# Exit: 0 healthy, 1 at least one problem (alerted), 64 misconfigured.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=alert-lib.sh
. "$SCRIPT_DIR/alert-lib.sh"
# shellcheck source=agencies-lib.sh
. "$SCRIPT_DIR/agencies-lib.sh"

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
TSV="${AGENCIES_TSV:-$BASE_DIR/etc/agencies.tsv}"
OK_MARKER="${SYNC_R2_OK_MARKER:-$BASE_DIR/.sync-r2.last-ok}"
SYNC_R2_MAX_STALE_DAYS="${SYNC_R2_MAX_STALE_DAYS-3}"
STATIC_MAX_STALE_DAYS="${STATIC_MAX_STALE_DAYS-3}"
# 20 intervals: long enough that a handful of consecutive failed fetches (the
# poller retries 4 times with backoff before giving up on one tick) is not an
# alert, short enough that a dead poller is caught inside half an hour even at
# the slowest configured 60s interval.
RT_STALE_FACTOR="${RT_STALE_FACTOR-20}"
# A GTFS-RT FeedMessage with nothing but a header is still tens of bytes, so
# only a truly zero-byte sample is unambiguously broken. Overnight feeds do
# legitimately shrink to header-only, which is why a larger floor is opt-in
# rather than the default.
MIN_RT_BYTES="${MIN_RT_BYTES-1}"
MIN_STATIC_BYTES="${MIN_STATIC_BYTES-1024}"

for var in SYNC_R2_MAX_STALE_DAYS STATIC_MAX_STALE_DAYS RT_STALE_FACTOR \
    MIN_RT_BYTES MIN_STATIC_BYTES; do
    value="${!var}"
    case "$value" in
        ''|*[!0-9]*)
            echo "health-check: $var must be a non-negative integer, got '$value'" >&2
            exit 64
            ;;
    esac
done

problems=""
problem_count=0
problem() {
    problems="${problems}- $1"$'\n'
    problem_count=$((problem_count + 1))
    echo "health-check: PROBLEM $1" >&2
}

# mtime in epoch seconds. GNU date handles `-r FILE`; BSD/macOS `date -r` means
# something else entirely there, so fall back to its `stat -f %m`.
file_epoch() { date -r "$1" +%s 2>/dev/null || stat -f %m "$1" 2>/dev/null; }

# Age of a file in seconds, or "" when its mtime cannot be read at all.
file_age() {
    local ts
    ts=$(file_epoch "$1")
    case "$ts" in
        ''|*[!0-9]*) return 0 ;;
    esac
    echo $(( NOW - ts ))
}

file_size() { wc -c < "$1" 2>/dev/null | tr -d ' '; }

NOW=$(date -u +%s)
HOST=$(hostname 2>/dev/null || echo unknown-host)

if [ ! -f "$TSV" ]; then
    # Not a "problem" in the per-agency sense: with no roster there is nothing
    # to check at all, and reporting "0 agencies, all healthy" would be a lie
    # of exactly the kind this script exists to prevent.
    echo "health-check: agencies roster $TSV is missing" >&2
    alert_report fail "collector health check cannot run on $HOST: agencies roster $TSV is missing"
    exit 64
fi

agencies=0
# `|| [ -n "$row" ]` processes a final row lacking a trailing newline, matching
# static-fetch.sh's reader.
while IFS= read -r row || [ -n "${row:-}" ]; do
    agency_row_is_data "$row" || continue
    split_agency_row "$row"
    agencies=$((agencies + 1))
    id="$agency_id"
    name="$agency_name"
    interval="$agency_interval"
    static="$agency_static_url"

    rt_dir="$BASE_DIR/data/$id/rt"
    newest_rt=$(ls -1d "$rt_dir"/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/TripUpdate_*.pb \
        2>/dev/null | sort | tail -1)
    if [ ! -d "$rt_dir" ]; then
        problem "a$id ($name): configured in agencies.tsv but has no RT directory ($rt_dir) — its poller has never run"
    elif [ -z "$newest_rt" ]; then
        problem "a$id ($name): no RT samples on disk under $rt_dir — its poller has stopped writing"
    else
        # Day dirs are YYYYMMDD and samples TripUpdate_HHMMSS.pb, both UTC and
        # both fixed-width, so the lexicographically last path is the newest.
        case "$interval" in
            ''|*[!0-9]*|0)
                problem "a$id ($name): invalid poll interval '$interval' in agencies.tsv — RT freshness cannot be checked"
                ;;
            *)
                rt_age=$(file_age "$newest_rt")
                rt_max=$(( interval * RT_STALE_FACTOR ))
                if [ -z "$rt_age" ]; then
                    problem "a$id ($name): could not read the mtime of $newest_rt"
                elif [ "$rt_age" -gt "$rt_max" ]; then
                    problem "a$id ($name): newest RT sample is ${rt_age}s old (limit ${rt_max}s = ${interval}s interval x $RT_STALE_FACTOR)"
                fi
                ;;
        esac
        rt_size=$(file_size "$newest_rt")
        if [ -z "$rt_size" ]; then
            problem "a$id ($name): could not read the size of $newest_rt"
        elif [ "$rt_size" -lt "$MIN_RT_BYTES" ]; then
            problem "a$id ($name): newest RT sample $(basename "$newest_rt") is ${rt_size} bytes (minimum $MIN_RT_BYTES) — the feed is answering with nothing"
        fi
    fi

    # An empty static_url is a real configuration (agency 1's static GTFS is
    # collected off-VM, see MIGRATION.md), not a missing one: checking it here
    # would alert forever on something this VM is not supposed to be doing.
    [ -n "${static:-}" ] || continue

    sdir="$BASE_DIR/data/$id/static"
    static_marker="$sdir/.static-last-ok"
    if [ ! -f "$static_marker" ]; then
        problem "a$id ($name): static GTFS fetch has never succeeded (no $static_marker)"
    else
        static_age=$(file_age "$static_marker")
        static_max=$(( STATIC_MAX_STALE_DAYS * 86400 ))
        if [ -z "$static_age" ]; then
            problem "a$id ($name): could not read the mtime of $static_marker"
        elif [ "$static_age" -gt "$static_max" ]; then
            problem "a$id ($name): last successful static GTFS fetch was $(( static_age / 86400 ))d ago (limit ${STATIC_MAX_STALE_DAYS}d) — static-fetch.sh is failing against $static"
        fi
    fi

    # -e follows the symlink, so a dangling latest.zip reads as missing here,
    # which is exactly the right verdict: prune or a stray rm took the target.
    if [ ! -e "$sdir/latest.zip" ]; then
        problem "a$id ($name): no current static GTFS ($sdir/latest.zip is missing or dangling)"
    else
        static_size=$(file_size "$sdir/latest.zip")
        if [ -z "$static_size" ]; then
            problem "a$id ($name): could not read the size of $sdir/latest.zip"
        elif [ "$static_size" -lt "$MIN_STATIC_BYTES" ]; then
            problem "a$id ($name): current static GTFS is only ${static_size} bytes (minimum $MIN_STATIC_BYTES) — upstream probably served an error page"
        fi
    fi
done < "$TSV"

if [ "$agencies" -eq 0 ]; then
    problem "no agencies are configured in $TSV — the collector is running but collecting nothing"
fi

if [ ! -f "$OK_MARKER" ]; then
    problem "R2 sync has never completed a fully-successful run (no $OK_MARKER) — prune.sh is refusing to run and local disk will keep growing"
else
    marker_age=$(file_age "$OK_MARKER")
    marker_max=$(( SYNC_R2_MAX_STALE_DAYS * 86400 ))
    if [ -z "$marker_age" ]; then
        problem "could not read the mtime of $OK_MARKER"
    elif [ "$marker_age" -gt "$marker_max" ]; then
        problem "R2 sync last fully succeeded $(( marker_age / 86400 ))d ago (limit ${SYNC_R2_MAX_STALE_DAYS}d) — nothing collected since then is confirmed mirrored, and prune.sh has stopped running"
    fi
fi

if [ "$problem_count" -gt 0 ]; then
    alert_report fail "$(printf 'collector health: %s problem(s) on %s\n\n%s' \
        "$problem_count" "$HOST" "$problems")"
    exit 1
fi

echo "health-check: OK — $agencies agencies, RT + static + R2 sync all fresh"
if ! alert_report ok "collector health OK on $HOST: $agencies agencies, RT + static + R2 sync all fresh"; then
    # The collector is fine but the all-clear did not land. Exiting nonzero
    # keeps that visible in cron.log; the endpoint's own period/grace alarm is
    # what actually catches a run of these.
    exit 1
fi
