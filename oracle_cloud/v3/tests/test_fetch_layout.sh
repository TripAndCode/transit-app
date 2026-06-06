#!/usr/bin/env bash
# fetch_archives.sh: v3 per-agency layout when COLLECTOR_DATA_DIR set,
# legacy flat layout otherwise. Uses local-path rsync (no ssh) via
# FETCH_TEST_LOCAL=1 hook.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh

REMOTE=$(mktemp -d); LOCAL=$(mktemp -d)
trap 'rm -rf "$REMOTE" "$LOCAL"' EXIT

# v3 layout on "remote"
mkdir -p "$REMOTE/collector/data/1/rt" "$REMOTE/collector/data/8/rt" \
         "$REMOTE/collector/data/1/static" "$REMOTE/collector/data/1/rt/20990101"
printf 'a' > "$REMOTE/collector/data/1/rt/20260601.tar.gz"
printf 'b' > "$REMOTE/collector/data/8/rt/20260601.tar.gz"
printf 'z' > "$REMOTE/collector/data/1/static/gtfs_static_20260601.zip"

FETCH_TEST_LOCAL=1 \
COLLECTOR_DATA_DIR="$REMOTE/collector/data" \
AGENCY_IDS="1 8" \
LOCAL_RT_DIR="$LOCAL/raw_archives" \
LOCAL_STATIC_DIR="$LOCAL/raw_archives_static" \
ORACLE_HOST=unused ORACLE_SSH_KEY_PATH=/dev/null \
    bash ../../../scripts/fetch_archives.sh

[ -f "$LOCAL/raw_archives/1/20260601.tar.gz" ] || fail "v3: agency 1 tarball not fetched"
[ -f "$LOCAL/raw_archives/8/20260601.tar.gz" ] || fail "v3: agency 8 tarball not fetched"
[ -f "$LOCAL/raw_archives_static/1/gtfs_static_20260601.zip" ] || fail "v3: static not fetched"
[ -d "$LOCAL/raw_archives/1/20990101" ] && fail "v3: live day dir must not be fetched"
pass "v3 per-agency fetch"

# Legacy flat layout still works when COLLECTOR_DATA_DIR unset.
REMOTE2=$(mktemp -d); LOCAL2=$(mktemp -d)
mkdir -p "$REMOTE2/archive" "$REMOTE2/static"
printf 'c' > "$REMOTE2/archive/20260601.tar.gz"
printf 'd' > "$REMOTE2/static/gtfs_static_20260601.zip"
FETCH_TEST_LOCAL=1 \
ORACLE_RT_DIR="$REMOTE2/archive" ORACLE_STATIC_DIR="$REMOTE2/static" \
LOCAL_RT_DIR="$LOCAL2/raw_archives" LOCAL_STATIC_DIR="$LOCAL2/raw_archives_static" \
ORACLE_HOST=unused ORACLE_SSH_KEY_PATH=/dev/null \
    bash ../../../scripts/fetch_archives.sh
[ -f "$LOCAL2/raw_archives/20260601.tar.gz" ] || fail "legacy flat fetch broken"
pass "legacy flat fetch intact"
rm -rf "$REMOTE2" "$LOCAL2"
