"""Each archived timetable's per-route scheduled headway medians (route_headway_by_content).

agg_route_headway_daily measures each (route, date)'s long gaps against the
median of the timetable that date's rows were ingested against. A timetable
can be read from Postgres only while it is loaded, so its medians are taken
wherever the archive is filled (load_static, analyze's backstop), by
agg_route_headway's own SQL at its registry version, and never deleted.
"""

import logging
from collections.abc import Iterable

from pipeline.static_content import static_parts

logger = logging.getLogger(__name__)

TABLE = "route_headway_by_content"


def _version() -> int:
    # agg_registry imports ch_aggregates, which imports static_archive: imported
    # here, at call time, so the archive modules stay importable on their own.
    from pipeline import agg_registry

    return agg_registry.TABLES["agg_route_headway"].version


def record_route_headways(conn, agency_id: int, key: str, parts: list[str]) -> int:
    """Record the loaded timetable's medians under *key* unless they are kept at
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
        medians = cur.fetchall()
    if not medians:
        return 0
    # A load replaces the timetable in one commit, so parts read again after
    # the medians still matching means the medians are *key*'s.
    if static_parts(agency_id, conn, True) != parts:
        logger.warning(f"  route headways: agency {agency_id}'s timetable changed before its medians were kept")
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {TABLE} (agency_id, content_hash, route_code, logic_version, scheduled_headway_median_sec) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            # In route order, so two recorders of the same content lock its rows alike.
            [(agency_id, key, route, version, median) for route, median in sorted(medians, key=lambda row: row[0])],
        )
    logger.info(f"  route headways: agency {agency_id} content {key}: {len(medians)} route(s) kept")
    return len(medians)


def route_medians(conn, agency_id: int, contents: Iterable[str]) -> dict[tuple[str, str], float | None]:
    """Each of *contents*' medians, keyed (content, route), at that content's highest
    version: a route the newest version no longer has is absent, not served at an
    older version's median."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT r.content_hash, r.route_code, r.scheduled_headway_median_sec FROM {TABLE} r "
            f"JOIN (SELECT content_hash, max(logic_version) AS logic_version FROM {TABLE} "
            "WHERE agency_id = %s AND content_hash = ANY(%s) GROUP BY content_hash) latest "
            "USING (content_hash, logic_version) WHERE r.agency_id = %s",
            (agency_id, sorted(set(contents)), agency_id),
        )
        return {(content, route): median for content, route, median in cur.fetchall()}
