#!/usr/bin/env bash
# static-fetch.sh: stores zip when content new/changed, discards when identical.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

printf '1\taomori\t30\thttp://feed.test/tu.pb\thttp://feed.test/static.zip\t\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
# Agency 2 has an EMPTY static_url followed by a non-empty ping_url -- the
# real shape of an agency whose static GTFS is collected off-VM. Tab is an IFS
# whitespace character, so a naive `IFS=$'\t' read` collapses the two tabs and
# hands the ping URL to static_url, i.e. curls a healthcheck endpoint and
# stores the response as that agency's GTFS zip.
printf '2\tnostatic\t30\thttp://feed.test/tu2.pb\t\thttp://ping.test/2\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"
day=$(date -u +%Y%m%d)

# First fetch: new content -> saved + latest.zip link.
CURL_BODY=v1 ../bin/static-fetch.sh
[ -f "$COLLECTOR_BASE/data/1/static/gtfs_static_$day.zip" ] || fail "first zip not saved"
[ -L "$COLLECTOR_BASE/data/1/static/latest.zip" ] || fail "latest.zip link missing"
[ -d "$COLLECTOR_BASE/data/2/static" ] && ls "$COLLECTOR_BASE/data/2/static/"*.zip 2>/dev/null \
    && fail "agency without static_url fetched something"
grep -q "http://ping.test/2" "$CURL_LOG" \
    && fail "the ping_url of an agency with no static_url was fetched as a GTFS zip"
pass "first static saved"

marker="$COLLECTOR_BASE/data/1/static/.static-last-ok"
[ -f "$marker" ] || fail "success marker not written after a successful fetch"
[ -f "$COLLECTOR_BASE/data/2/static/.static-last-ok" ] \
    && fail "agency without static_url got a success marker"
pass "a successful fetch writes the per-agency success marker"

# Second fetch same content: discarded (no extra file beyond the first).
old_ts=$(date -v-400d +%Y%m%d%H%M 2>/dev/null || date -d "400 days ago" +%Y%m%d%H%M)
touch -t "$old_ts" "$marker"
CURL_BODY=v1 ../bin/static-fetch.sh
n=$(ls "$COLLECTOR_BASE/data/1/static/"gtfs_static_*.zip | wc -l | tr -d ' ')
[ "$n" -eq 1 ] || fail "unchanged content was re-saved ($n files)"
pass "unchanged static discarded"

# ...but the marker still moves: it attests to the fetch, not to the content.
# health-check.sh reads it precisely because latest.zip's own mtime cannot
# tell a stable upstream apart from a fetch that stopped working.
marker_epoch=$(date -r "$marker" +%s 2>/dev/null || stat -f %m "$marker")
[ $(( $(date -u +%s) - marker_epoch )) -lt 3600 ] \
    || fail "unchanged content left the success marker stale"
pass "an unchanged fetch still refreshes the success marker"

# Changed content: new file would share today's name -> must still update latest target content.
CURL_BODY=v2 ../bin/static-fetch.sh
grep -q "PBDATA-v2" "$COLLECTOR_BASE/data/1/static/latest.zip" || fail "latest not updated on change"
pass "changed static updates latest"

teardown_base
setup_base
trap teardown_base EXIT

# TSV last row lacks a trailing newline (hand-edited file): that agency must still process.
printf '3\thirosaki\t30\thttp://feed.test/tu3.pb\thttp://feed.test/static3.zip\t' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
day=$(date -u +%Y%m%d)
CURL_BODY=v3 ../bin/static-fetch.sh
[ -f "$COLLECTOR_BASE/data/3/static/gtfs_static_$day.zip" ] || fail "newline-less last row not processed"
pass "newline-less TSV tail processed"

# A failing fetch must exit nonzero (cron-wrap.sh turns that into an alert)
# without stopping the agencies after it in the roster, and must leave the
# failing agency's marker alone so health-check.sh sees it go stale.
teardown_base
setup_base
trap teardown_base EXIT
printf '1\taomori\t30\thttp://feed.test/tu.pb\thttp://feed.test/static.zip\t\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
printf '8\thiroden\t60\thttp://feed.test/tu8.pb\thttp://feed.test/static8.zip\t\n' \
    >> "$COLLECTOR_BASE/etc/agencies.tsv"
day=$(date -u +%Y%m%d)
set +e
CURL_FAIL=1 ../bin/static-fetch.sh > "$COLLECTOR_BASE/fetch.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "a failed static fetch should exit 1, got $rc"
[ -f "$COLLECTOR_BASE/data/1/static/.static-last-ok" ] \
    && fail "success marker written despite the fetch failing"
grep -q "a8.*static fetch FAILED" "$COLLECTOR_BASE/fetch.out" \
    || fail "agency 8 was skipped after agency 1's fetch failed"
ls "$COLLECTOR_BASE/data/1/static/".dl.* 2>/dev/null && fail "temp download left behind"
pass "a failed static fetch exits nonzero without skipping the rest"
