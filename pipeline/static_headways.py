"""Each archived timetable's per-route scheduled headways (route_headway_by_content):
their median, sum and sum of squares.

agg_route_headway_daily judges each (route, date) by the timetable that date's
rows were filed under: its long gaps against that timetable's median, its
high-frequency test by the same median, and its scheduled wait by the sums,
which pool across days and timetables by addition where a median or a mean
wait would not. A timetable can be read from Postgres only while it is
loaded, so its headways are taken wherever the archive is filled, by
agg_route_headway's own SQL at its registry version, and never deleted.
load_static records every agency's outgoing timetable, its last chance before
the wipe, whatever the agency ingests today; analyze's backstop only an agency
whose daily headways it builds. A timetable kept at a version before the sums
were kept has its median only.
"""

import logging
from collections.abc import Iterable

from pipeline.headways import ScheduledHeadways
from pipeline.static_content import static_parts

logger = logging.getLogger(__name__)

TABLE = "route_headway_by_content"


def _version() -> int:
    # agg_registry imports ch_aggregates, which imports static_archive: imported
    # here, at call time, so the archive modules stay importable on their own.
    from pipeline import agg_registry

    return agg_registry.TABLES["agg_route_headway"].version


def record_route_headways(conn, agency_id: int, key: str, parts: list[str]) -> int:
    """Record the loaded timetable's headways under *key* unless they are kept at
    the current version; the rows inserted. Nothing is recorded when the
    timetable changed since *parts* were read (a concurrent load_static)."""
    from pipeline import agg_sql

    version = _version()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {TABLE} WHERE agency_id = %s AND content_hash = %s AND logic_version = %s LIMIT 1",
            (agency_id, key, version),
        )
        if cur.fetchone():
            return 0
        cur.execute(agg_sql.route_headway_sql(all_routes=True), {"agency_id": agency_id})
        headways = cur.fetchall()
    if not headways:
        return 0
    # A load replaces the timetable in one commit, so parts read again after
    # the headways still matching means the headways are *key*'s.
    if static_parts(agency_id, conn, True) != parts:
        logger.warning(f"  route headways: agency {agency_id}'s timetable changed before its headways were kept")
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {TABLE} (agency_id, content_hash, route_code, logic_version, scheduled_headway_median_sec, "
            "scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            # In route order, so two recorders of the same content lock its rows alike.
            [(agency_id, key, route, version, *kept) for route, *kept in sorted(headways, key=lambda row: row[0])],
        )
    logger.info(f"  route headways: agency {agency_id} content {key}: {len(headways)} route(s) kept")
    return len(headways)


def route_headways(conn, agency_id: int, contents: Iterable[str]) -> dict[tuple[str, str], ScheduledHeadways]:
    """Each of *contents*' kept headways, keyed (content, route), at that content's
    highest version: a route the newest version no longer has is absent, not
    served at an older version's figures."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT r.content_hash, r.route_code, r.scheduled_headway_median_sec, r.scheduled_headway_sum_sec, "
            f"r.scheduled_headway_sumsq_sec2 FROM {TABLE} r "
            f"JOIN (SELECT content_hash, max(logic_version) AS logic_version FROM {TABLE} "
            "WHERE agency_id = %s AND content_hash = ANY(%s) GROUP BY content_hash) latest "
            "USING (content_hash, logic_version) WHERE r.agency_id = %s",
            (agency_id, sorted(set(contents)), agency_id),
        )
        return {(content, route): ScheduledHeadways(*figures) for content, route, *figures in cur.fetchall()}


def changed_routes(kept: dict[tuple[str, str], ScheduledHeadways], before: str, after: str) -> list[str]:
    """The routes whose kept headways differ between contents *before* and
    *after*: another median or sum, NULL against a value, or kept for one only."""

    def of(content: str, route: str) -> tuple[bool, ScheduledHeadways | None]:
        return (content, route) in kept, kept.get((content, route))

    routes = {route for content, route in kept if content in (before, after)}
    return sorted(route for route in routes if of(before, route) != of(after, route))
