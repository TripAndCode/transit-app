#!/usr/bin/env bash
# static-fetch.sh: stores zip when content new/changed, discards when identical.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

printf '1\taomori\t30\thttp://feed.test/tu.pb\thttp://feed.test/static.zip\t\n' \
    > "$COLLECTOR_BASE/etc/agencies.tsv"
printf '2\tnostatic\t30\thttp://feed.test/tu2.pb\t\t\n' >> "$COLLECTOR_BASE/etc/agencies.tsv"
day=$(date -u +%Y%m%d)

# First fetch: new content -> saved + latest.zip link.
CURL_BODY=v1 ../bin/static-fetch.sh
[ -f "$COLLECTOR_BASE/data/1/static/gtfs_static_$day.zip" ] || fail "first zip not saved"
[ -L "$COLLECTOR_BASE/data/1/static/latest.zip" ] || fail "latest.zip link missing"
[ -d "$COLLECTOR_BASE/data/2/static" ] && ls "$COLLECTOR_BASE/data/2/static/"*.zip 2>/dev/null \
    && fail "agency without static_url fetched something"
pass "first static saved"

# Second fetch same content: discarded (no extra file beyond the first).
CURL_BODY=v1 ../bin/static-fetch.sh
n=$(ls "$COLLECTOR_BASE/data/1/static/"gtfs_static_*.zip | wc -l | tr -d ' ')
[ "$n" -eq 1 ] || fail "unchanged content was re-saved ($n files)"
pass "unchanged static discarded"

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
