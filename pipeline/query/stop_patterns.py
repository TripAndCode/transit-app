"""Complete current-schedule stop lists for patterns observed in a scoped window."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any

from api.range import RangeCtx
from pipeline.reports.filters import _dedup_cte_ch, _round2

MAX_GROUPS = 20000
# A single observed (trip_id, stop_sequence) group can pull in a whole trip's full
# static timetable, so the schedule-row fetch needs its own, larger ceiling rather
# than reusing MAX_GROUPS -- otherwise routes with long trips trip this bound long
# before MAX_GROUPS itself is approached.
MAX_SCHEDULE_ROWS = 200000
MAX_STOPS = 2000
COLUMNS = ["pattern_id", "pattern_name", "stop_sequence", "stop_id", "stop_name", "avg_min", "samples"]


class PatternWindowTooLarge(ValueError):
    pass


@dataclass
class _Pattern:
    stops: list[dict[str, Any]]
    totals: dict[int, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[int, int] = field(default_factory=lambda: defaultdict(int))


def assemble_patterns(observations: list, scheduled: list) -> list:
    trips = defaultdict(list)
    for stop in scheduled:
        trips[stop["trip_id"]].append(stop)
    stats: dict[Any, dict[Any, tuple[float, int]]] = defaultdict(dict)
    for trip, seq, total, count in observations:
        stats[trip][seq] = (total, count)
    patterns: dict[str, _Pattern] = {}
    for trip_id in sorted(trips):
        stops = sorted(trips[trip_id], key=lambda row: row["stop_sequence"])
        signature = [(row["stop_sequence"], row["stop_id"]) for row in stops]
        # A schedule mismatch cannot safely be represented as a complete route.
        if len({seq for seq, _ in signature}) != len(stops):
            continue
        if set(stats[trip_id]) - {seq for seq, _ in signature}:
            continue
        identity = (stops[0]["route_id"], signature)
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
        if key not in patterns:
            patterns[key] = _Pattern(stops)
        pattern = patterns[key]
        for seq, _ in signature:
            total, count = stats[trip_id].get(seq, (0, 0))
            pattern.totals[seq] += total
            pattern.counts[seq] += count
    rows = []
    for key, pattern in sorted(patterns.items()):
        stops = pattern.stops
        label = f"{stops[0]['stop_name']} → {stops[-1]['stop_name']} ({len(stops)})"
        for stop in stops:
            seq = stop["stop_sequence"]
            count = pattern.counts[seq]
            minutes = _round2(pattern.totals[seq] / count / 60) if count else None
            rows.append([key, label, seq, stop["stop_id"], stop["stop_name"], minutes, count])
    return rows


async def query_stop_patterns(agency_id: int, ctx: RangeCtx, conn, ch=None, *, route: str) -> list:
    if ch is None or (ctx.routes and route not in ctx.routes):
        return []
    cte, params = _dedup_cte_ch(replace(ctx, routes=(route,)))
    result = await ch.query(
        f"WITH {cte} SELECT trip_id, stop_sequence, sum(dep_delay), count() "
        "FROM deduped WHERE stop_sequence IS NOT NULL "
        "GROUP BY trip_id, stop_sequence ORDER BY trip_id, stop_sequence "
        "LIMIT {pattern_limit:UInt32}",
        parameters={"agency_id": agency_id, "pattern_limit": MAX_GROUPS + 1, **params},
    )
    observations = result.result_rows
    if not observations:
        return []
    if len(observations) > MAX_GROUPS:
        raise PatternWindowTooLarge
    scheduled = await conn.fetch(
        "SELECT st.trip_id, t.route_id, st.stop_sequence, st.stop_id, "
        "COALESCE(s.stop_name, st.stop_id) AS stop_name "
        "FROM static_stop_times st JOIN static_trips t "
        "ON t.agency_id = st.agency_id AND t.trip_id = st.trip_id "
        "LEFT JOIN static_stops s ON s.agency_id = st.agency_id AND s.stop_id = st.stop_id "
        "WHERE st.agency_id = $1 AND st.trip_id = ANY($2::text[]) "
        "ORDER BY st.trip_id, st.stop_sequence LIMIT $3",
        agency_id,
        sorted({row[0] for row in observations}),
        MAX_SCHEDULE_ROWS + 1,
    )
    if len(scheduled) > MAX_SCHEDULE_ROWS:
        raise PatternWindowTooLarge
    rows = assemble_patterns(observations, scheduled)
    if len(rows) > MAX_STOPS:
        raise PatternWindowTooLarge
    return rows
