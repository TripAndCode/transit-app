#!/usr/bin/env bash
# prune.sh: deletes RT tarballs older than RETENTION_DAYS; keeps young ones,
# live day dirs, and the latest.zip target regardless of age. Refuses to
# run at all unless sync-r2.sh's success marker is fresh.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

rt="$COLLECTOR_BASE/data/1/rt"; sdir="$COLLECTOR_BASE/data/1/static"
mkdir -p "$rt/29990101" "$sdir"
printf 'old' > "$rt/20200101.tar.gz"
printf 'new' > "$rt/29990102.tar.gz"
printf 'oldzip' > "$sdir/gtfs_static_20200101.zip"
printf 'livezip' > "$sdir/gtfs_static_20200102.zip"
ln -sfn gtfs_static_20200102.zip "$sdir/latest.zip"
# Age the old files (mtime 400 days back).
old_ts=$(date -v-400d +%Y%m%d%H%M 2>/dev/null || date -d "400 days ago" +%Y%m%d%H%M)
touch -t "$old_ts" "$rt/20200101.tar.gz" "$sdir/gtfs_static_20200101.zip" "$sdir/gtfs_static_20200102.zip"

# No sync-r2.sh success marker yet -> refuses to run, deletes nothing.
if RETENTION_DAYS=90 STATIC_RETENTION_DAYS=365 ../bin/prune.sh 2>/dev/null; then
    fail "prune.sh should refuse to run without a sync-r2.sh success marker"
fi
[ -f "$rt/20200101.tar.gz" ] || fail "prune.sh deleted files despite missing marker"
pass "prune.sh refuses to run when the sync-r2.sh marker is missing"

# Stale marker (older than SYNC_R2_MAX_STALE_DAYS) -> also refuses.
touch -t "$old_ts" "$COLLECTOR_BASE/.sync-r2.last-ok"
if RETENTION_DAYS=90 STATIC_RETENTION_DAYS=365 ../bin/prune.sh 2>/dev/null; then
    fail "prune.sh should refuse to run when the marker is stale"
fi
[ -f "$rt/20200101.tar.gz" ] || fail "prune.sh deleted files despite a stale marker"
pass "prune.sh refuses to run when the sync-r2.sh marker is stale"

# Fresh marker -> proceeds exactly as before.
touch "$COLLECTOR_BASE/.sync-r2.last-ok"
RETENTION_DAYS=90 STATIC_RETENTION_DAYS=365 ../bin/prune.sh

[ -f "$rt/20200101.tar.gz" ] && fail "old RT tarball survived"
[ -f "$rt/29990102.tar.gz" ] || fail "young RT tarball deleted"
[ -d "$rt/29990101" ] || fail "live day dir deleted"
[ -f "$sdir/gtfs_static_20200101.zip" ] && fail "old static survived"
[ -f "$sdir/gtfs_static_20200102.zip" ] || fail "latest.zip target deleted despite age"
pass "prune respects retention + latest target once the marker is fresh"
