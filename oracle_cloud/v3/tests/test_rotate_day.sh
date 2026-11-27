#!/usr/bin/env bash
# rotate-day.sh: tars closed UTC days, never touches today, idempotent.
set -euo pipefail
cd "$(dirname "$0")"
source ./helpers.sh
setup_base
trap teardown_base EXIT

today=$(date -u +%Y%m%d)
yday=$(date -u -v-1d +%Y%m%d 2>/dev/null || date -u -d "yesterday" +%Y%m%d)

for id in 1 8; do
    mkdir -p "$COLLECTOR_BASE/data/$id/rt/$yday" "$COLLECTOR_BASE/data/$id/rt/$today"
    printf 'x' > "$COLLECTOR_BASE/data/$id/rt/$yday/TripUpdate_010101.pb"
    printf 'y' > "$COLLECTOR_BASE/data/$id/rt/$today/TripUpdate_020202.pb"
done

../bin/rotate-day.sh

for id in 1 8; do
    [ -f "$COLLECTOR_BASE/data/$id/rt/$yday.tar.gz" ] || fail "a$id: $yday.tar.gz missing"
    [ -d "$COLLECTOR_BASE/data/$id/rt/$yday" ] && fail "a$id: $yday dir not removed"
    [ -d "$COLLECTOR_BASE/data/$id/rt/$today" ] || fail "a$id: today dir was touched"
    tar -tzf "$COLLECTOR_BASE/data/$id/rt/$yday.tar.gz" | grep -q "TripUpdate_010101.pb" \
        || fail "a$id: tarball content wrong"
done
pass "rotate tars closed days only"

# Idempotency: second run is a no-op and exits 0.
../bin/rotate-day.sh
[ -f "$COLLECTOR_BASE/data/1/rt/$yday.tar.gz" ] || fail "tarball lost on re-run"
pass "rotate idempotent"

# VehiclePositions days rotate beside RT ones, into the agency's own vp/.
mkdir -p "$COLLECTOR_BASE/data/8/vp/$yday" "$COLLECTOR_BASE/data/8/vp/$today"
printf 'v' > "$COLLECTOR_BASE/data/8/vp/$yday/VehiclePosition_010101.pb"
printf 'w' > "$COLLECTOR_BASE/data/8/vp/$today/VehiclePosition_020202.pb"
../bin/rotate-day.sh > "$COLLECTOR_BASE/rotate.out"
vp_tar="$COLLECTOR_BASE/data/8/vp/$yday.tar.gz"
[ -f "$vp_tar" ] || fail "a8: vp/$yday.tar.gz missing"
[ -d "$COLLECTOR_BASE/data/8/vp/$yday" ] && fail "a8: vp $yday dir not removed"
[ -d "$COLLECTOR_BASE/data/8/vp/$today" ] || fail "a8: today's vp dir was touched"
[ "$(tar -tzf "$vp_tar" | grep -c .)" -ge 1 ] || fail "a8: vp tarball is empty"
tar -tzf "$vp_tar" | grep -q "^$yday/VehiclePosition_010101.pb$" || fail "a8: vp tarball content wrong"
tar -tzf "$vp_tar" | grep -q TripUpdate && fail "a8: RT samples leaked into the vp tarball"
[ -e "$COLLECTOR_BASE/data/1/vp" ] && fail "a1 has no VP feed but got a vp/ dir"
pass "rotate tars closed VehiclePositions days under vp/"
