"""GTFS static loader.

Reads a GTFS static zip (``stops.txt``, ``stop_times.txt``, ``trips.txt``,
``routes.txt``, ``calendar_dates.txt``, ``shapes.txt``, ``calendar.txt``) and writes each file
into its corresponding ``static_*`` table for one agency. The loader is
idempotent: a per-table ``DELETE WHERE agency_id = ...`` runs before each
file's rows are inserted, so re-running on the same agency replaces rather
than duplicates.

Two tables get geometry-aware handling: ``static_stops`` builds a
``geometry(Point)`` per row, and ``static_shapes`` groups points by
``shape_id`` and builds a ``geometry(LineString)`` per shape via PostGIS
``ST_MakeLine``. Other tables use a plain ``execute_values`` bulk insert.
Loading ``stops.txt`` also rebuilds ``stop_clusters`` (the heatmap's
same-named-platform grouping) from the new rows in the same transaction.

Every load then recomputes each route's terminal names and loop/both-ways
flags from what is now in place, reading GTFS-JP ``routes_jp.txt`` when the
zip has it (``pipeline.route_terminals``).

Before replacing anything, a load saves and commits the outgoing timetable's
planned trips for the days it covers up to today and before the incoming
timetable starts (``pipeline.planned_trips``), so a reload never takes away the
plan of a day that has run, and a day the incoming timetable covers keeps the
incoming plan.
"""

import csv
import io
import logging
import pathlib
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime

from psycopg2.extras import execute_values

from pipeline.planned_trips import capture_planned_trips
from pipeline.route_terminals import load_route_terminals
from pipeline.stop_clusters import rebuild_stop_clusters

logger = logging.getLogger(__name__)

# calendar.txt's weekly flags, Monday first (the order of ``date.weekday()``).
WEEKDAY_COLUMNS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_STATIC_FILE_MAP = [
    ("stops.txt", "static_stops", ["stop_id", "stop_name", "stop_lat", "stop_lon", "stop_code", "platform_code"]),
    ("stop_times.txt", "static_stop_times", ["trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"]),
    ("trips.txt", "static_trips", ["trip_id", "route_id", "trip_headsign", "shape_id", "service_id", "direction_id"]),
    ("routes.txt", "static_routes", ["route_id", "route_short_name", "route_long_name"]),
    ("calendar_dates.txt", "static_calendar_dates", ["service_id", "date", "exception_type"]),
    ("shapes.txt", "static_shapes", ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]),
    ("calendar.txt", "static_calendar", ["service_id", *WEEKDAY_COLUMNS, "start_date", "end_date"]),
]

_DB_COLS = {
    "static_stop_times": ["agency_id", "trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"],
    "static_trips": [
        "agency_id",
        "trip_id",
        "route_id",
        "trip_headsign",
        "shape_id",
        "service_id",
        "direction_id",
        "static_version_id",
    ],
    "static_routes": ["agency_id", "route_id", "route_short_name", "route_long_name"],
    "static_calendar_dates": ["agency_id", "service_id", "date", "exception_type"],
    "static_calendar": ["agency_id", "service_id", *WEEKDAY_COLUMNS, "start_date", "end_date"],
}


def resolve_static_zip(path: str) -> pathlib.Path:
    """The zip *path* names: the file itself, or the most recently named
    ``*static*.zip`` inside it when it is a directory.

    Raises:
        FileNotFoundError: If no zip resolves.
    """
    p = pathlib.Path(path)
    if p.is_dir():
        # Match both legacy *_static.zip and the Oracle scraper's gtfs_static_*.zip
        zips = sorted(p.glob("*static*.zip"))
        if not zips:
            raise FileNotFoundError(f"No *static*.zip found in {p}")
        p = zips[-1]
        logger.info(f"Using: {p.name}")
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return p


def read_static_rows(zf: zipfile.ZipFile, filename: str) -> list[list]:
    """*filename*'s rows as the loader reads them: the columns
    ``_STATIC_FILE_MAP`` lists for it, in that order, each the file's raw text
    or None where the file has no such column. The caller checks that the zip
    holds the file."""
    csv_cols = next(cols for name, _table, cols in _STATIC_FILE_MAP if name == filename)
    with zf.open(filename) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        return [[row.get(c) for c in csv_cols] for row in reader]


def _calendar_rows(raw_rows: list[list]) -> list[list]:
    """calendar.txt rows typed for ``static_calendar``, malformed ones dropped.

    Parsed here rather than cast by Postgres: the whole static set loads in one
    transaction, so one unparseable row would otherwise abort every table.
    """
    rows: list[list] = []
    for raw in raw_rows:
        service_id, *flags, start, end = [(v or "").strip() for v in raw]
        try:
            if not service_id or any(f not in {"0", "1"} for f in flags):
                raise ValueError
            start_date: date = datetime.strptime(start, "%Y%m%d").date()
            end_date: date = datetime.strptime(end, "%Y%m%d").date()
        except ValueError:
            continue
        rows.append([service_id, *(f == "1" for f in flags), start_date, end_date])
    if len(rows) < len(raw_rows):
        logger.warning(f"  calendar.txt: skipped {len(raw_rows) - len(rows)} malformed row(s)")
    return rows


def _first_covered_day(calendar_rows: list[list], calendar_dates_rows: list[list]) -> date | None:
    """The first day a timetable covers, under the rule ``pipeline.planned_trips``
    counts coverage by: a ``calendar.txt`` service's start date, or a date a
    ``calendar_dates.txt`` row names. A ``calendar_dates.txt`` date that is not
    eight ASCII digits naming a real day matches no day there, so it covers
    nothing here either."""
    days = [row[-2] for row in calendar_rows]
    for _service_id, raw, _exception_type in calendar_dates_rows:
        if raw and re.fullmatch(r"[0-9]{8}", raw):
            try:
                days.append(datetime.strptime(raw, "%Y%m%d").date())
            except ValueError:
                continue
    return min(days, default=None)


def _count_calendar_trips(cur, agency_id: int) -> None:
    """Store each calendar service's trip count, the weight of its vote for a
    date's timetable class (``pipeline.day_types``), so reports read it
    instead of aggregating ``static_trips`` per request. Rows were just
    inserted at the column's default of 0, so a service with no trips keeps it.
    """
    cur.execute(
        "UPDATE static_calendar c SET trips = t.trips "
        "FROM (SELECT service_id, COUNT(*) AS trips FROM static_trips WHERE agency_id = %s GROUP BY service_id) t "
        "WHERE c.agency_id = %s AND c.service_id = t.service_id",
        (agency_id, agency_id),
    )


def load_static(path: str, agency_id: int, conn) -> None:
    """Load a GTFS static zip into the ``static_*`` tables for ``agency_id``.

    Args:
        path: Either a zip file path or a directory containing one.
            When a directory is given, the most recently named
            ``*static*.zip`` inside it is picked.
        agency_id: The agency id rows are scoped to.
        conn: A psycopg2 connection. load_static first commits the
            outgoing timetable's planned trips, then commits the load as one
            transaction, then runs ANALYZE and commits again, before
            returning; any uncommitted work the caller already had open on
            ``conn`` is committed with the first of these, so it cannot be
            rolled back alongside the load. It never rolls back: if it
            raises, the caller rolls back before reusing ``conn``, and a
            failure before the load's commit leaves none of the load in
            place. The saved planned trips stay, and stay right: the
            timetable they came from is still the one loaded.

    Raises:
        FileNotFoundError: If ``path`` does not resolve to a zip file
            (or no matching zip is found inside a directory).

    Files missing from the zip are skipped with a logged ``not in zip``
    line; this lets agencies without ``shapes.txt`` (or any other optional
    file) load successfully. Each loaded table is cleared for this
    agency before insert, so the function is safe to re-run. The
    exceptions to "skipped" are ``static_calendar`` and
    ``static_calendar_dates``: a missing ``calendar.txt`` or
    ``calendar_dates.txt`` clears its table, so an earlier feed's
    timetables and service exceptions cannot classify this feed's dates.
    """
    p = resolve_static_zip(path)

    # The loaded zip's filename stem (e.g. "gtfs_static_20260101" or the
    # legacy "hiroden_static") is the only identifier that survives past this
    # function as a durable marker of which static feed version an RT row's
    # static_join matched against -- static_trips.static_version_id gets this
    # same value for every row loaded in this one call.
    static_version_id = p.stem

    with zipfile.ZipFile(p) as zf, conn.cursor() as cur:
        names_in_zip = set(zf.namelist())
        calendar_rows = _calendar_rows(read_static_rows(zf, "calendar.txt")) if "calendar.txt" in names_in_zip else []
        calendar_dates_rows = read_static_rows(zf, "calendar_dates.txt") if "calendar_dates.txt" in names_in_zip else []
        # Before any table is replaced, trips.txt's included: the outgoing
        # timetable's plan is its calendar and its trips together. The days
        # from the incoming timetable's first are left to it, and the capture
        # commits now so a concurrent analyze's capture never waits on this load.
        capture_planned_trips(conn, agency_id, before=_first_covered_day(calendar_rows, calendar_dates_rows))
        for filename, table, _csv_cols in _STATIC_FILE_MAP:
            if filename not in names_in_zip:
                logger.warning(f"  {filename} not in zip — skipped")
                if table in ("static_calendar", "static_calendar_dates"):
                    cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))
                continue

            cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))

            raw_rows = read_static_rows(zf, filename)

            if table == "static_stops":
                for row in raw_rows:
                    stop_id, stop_name = row[0], row[1]
                    try:
                        lat, lon = float(row[2]), float(row[3])  # type: ignore[arg-type]  # None -> TypeError, caught below
                    except (TypeError, ValueError):
                        lat, lon = None, None
                    # Optional GTFS fields — present in Aomori's feed
                    # ("②のりば", platform "2"), absent in some others.
                    stop_code = (row[4] or None) if len(row) > 4 else None
                    platform_code = (row[5] or None) if len(row) > 5 else None
                    cur.execute(
                        "INSERT INTO static_stops "
                        "(agency_id, stop_id, stop_name, stop_lat, stop_lon, geom, stop_code, platform_code) "
                        "VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s) "
                        "ON CONFLICT (agency_id, stop_id) DO UPDATE SET "
                        "stop_name=EXCLUDED.stop_name, stop_lat=EXCLUDED.stop_lat, "
                        "stop_lon=EXCLUDED.stop_lon, geom=EXCLUDED.geom, "
                        "stop_code=EXCLUDED.stop_code, platform_code=EXCLUDED.platform_code",
                        [agency_id, stop_id, stop_name, lat, lon, lon, lat, stop_code, platform_code],
                    )
                clustered = rebuild_stop_clusters(cur, agency_id)
                logger.info(f"  stop_clusters: {clustered:,} rows")
            elif table == "static_shapes":
                by_shape: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
                skipped = 0
                for row in raw_rows:
                    shape_id = row[0]
                    try:
                        lat = float(row[1])  # type: ignore[arg-type]  # None -> TypeError, caught below
                        lon = float(row[2])  # type: ignore[arg-type]
                        seq = int(row[3])  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        skipped += 1
                        continue
                    if shape_id:
                        by_shape[shape_id].append((seq, lon, lat))

                if skipped:
                    logger.warning(f"  shapes.txt: skipped {skipped} malformed row(s)")

                for shape_id, pts in by_shape.items():
                    pts.sort(key=lambda t: t[0])
                    if len(pts) < 2:
                        # PostGIS LineString needs >= 2 points
                        continue
                    flat: list[float] = []
                    for _, lon, lat in pts:
                        flat.extend([lon, lat])
                    placeholders = ",".join("ST_MakePoint(%s, %s)" for _ in range(len(pts)))
                    cur.execute(
                        f"INSERT INTO static_shapes (agency_id, shape_id, geom) "
                        f"VALUES (%s, %s, ST_SetSRID(ST_MakeLine(ARRAY[{placeholders}]), 4326)) "
                        f"ON CONFLICT (agency_id, shape_id) DO UPDATE SET geom = EXCLUDED.geom",
                        [agency_id, shape_id, *flat],
                    )
            else:
                db_cols = _DB_COLS[table]
                col_list = ", ".join(db_cols)
                if table == "static_trips":
                    for row in raw_rows:
                        direction = row[-1]
                        row[-1] = int(direction) if direction in {"0", "1"} else None
                    pg_rows = [[agency_id, *row, static_version_id] for row in raw_rows]
                elif table == "static_calendar":
                    pg_rows = [[agency_id, *row] for row in calendar_rows]
                else:
                    pg_rows = [[agency_id, *row] for row in raw_rows]
                # RETURNING lets us count how many rows actually landed vs were deduped
                inserted = execute_values(
                    cur,
                    f"INSERT INTO {table} ({col_list}) VALUES %s ON CONFLICT DO NOTHING RETURNING 1",
                    pg_rows,
                    fetch=True,
                )
                skipped = len(pg_rows) - len(inserted)
                if skipped:
                    logger.warning(f"  WARNING: {skipped} duplicate rows skipped in {table}")

            logger.info(f"  {table}: {len(raw_rows):,} rows")

        if "trips.txt" in names_in_zip:
            # Without it static_trips still holds the previous feed's trips, so
            # every service keeps the default weight rather than stale counts.
            _count_calendar_trips(cur, agency_id)
        changed = load_route_terminals(zf, names_in_zip, cur, agency_id)
        logger.info(f"  route terminals: {changed:,} routes changed")

    # Commit once, after every table — a mid-load failure then rolls back the
    # whole static set rather than leaving a partial one (which `_static_loaded`,
    # gated only on static_stops, would treat as a complete load).
    conn.commit()

    # static_join.parse_feed's keyed lookups depend on per-agency planner
    # statistics for static_stop_times and static_trips. A freshly inserted
    # agency_id is exactly the case those statistics are stalest for
    # (autovacuum's autoanalyze is asynchronous and has a row-count threshold
    # before it fires), and a stale/absent per-agency estimate can steer the
    # planner off the primary-key probes those lookups are meant to be.
    # ANALYZE is a bounded sample, not a full-table pass, so this stays cheap
    # regardless of how large the two tables have grown across all agencies.
    with conn.cursor() as cur:
        cur.execute("ANALYZE static_trips")
        cur.execute("ANALYZE static_stop_times")
    conn.commit()

    logger.info("Static data loaded.")
