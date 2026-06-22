"""GTFS static loader.

Reads a GTFS static zip (``stops.txt``, ``stop_times.txt``, ``trips.txt``,
``routes.txt``, ``calendar_dates.txt``, ``shapes.txt``) and writes each file
into its corresponding ``static_*`` table for one agency. The loader is
idempotent: a per-table ``DELETE WHERE agency_id = ...`` runs before each
file's rows are inserted, so re-running on the same agency replaces rather
than duplicates.

Two tables get geometry-aware handling: ``static_stops`` builds a
``geometry(Point)`` per row, and ``static_shapes`` groups points by
``shape_id`` and builds a ``geometry(LineString)`` per shape via PostGIS
``ST_MakeLine``. Other tables use a plain ``execute_values`` bulk insert.
"""

import csv
import io
import logging
import pathlib
import zipfile
from collections import defaultdict

from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

_STATIC_FILE_MAP = [
    ("stops.txt", "static_stops", ["stop_id", "stop_name", "stop_lat", "stop_lon", "stop_code", "platform_code"]),
    ("stop_times.txt", "static_stop_times", ["trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"]),
    ("trips.txt", "static_trips", ["trip_id", "route_id", "trip_headsign", "shape_id", "service_id"]),
    ("routes.txt", "static_routes", ["route_id", "route_short_name", "route_long_name"]),
    ("calendar_dates.txt", "static_calendar_dates", ["service_id", "date", "exception_type"]),
    ("shapes.txt", "static_shapes", ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]),
]

_DB_COLS = {
    "static_stop_times": ["agency_id", "trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"],
    "static_trips": ["agency_id", "trip_id", "route_id", "trip_headsign", "shape_id", "service_id"],
    "static_routes": ["agency_id", "route_id", "route_short_name", "route_long_name"],
    "static_calendar_dates": ["agency_id", "service_id", "date", "exception_type"],
}


def load_static(path: str, agency_id: int, conn) -> None:
    """Load a GTFS static zip into the ``static_*`` tables for ``agency_id``.

    Args:
        path: Either a zip file path or a directory containing one.
            When a directory is given, the most recently named
            ``*static*.zip`` inside it is picked.
        agency_id: The agency id rows are scoped to.
        conn: A psycopg2 connection. The caller commits.

    Raises:
        FileNotFoundError: If ``path`` does not resolve to a zip file
            (or no matching zip is found inside a directory).

    Files missing from the zip are skipped with a logged ``not in zip``
    line; this lets agencies without ``shapes.txt`` (or any other optional
    file) load successfully. Each loaded table is cleared for this
    agency before insert, so the function is safe to re-run.
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

    with zipfile.ZipFile(p) as zf, conn.cursor() as cur:
        names_in_zip = set(zf.namelist())
        for filename, table, csv_cols in _STATIC_FILE_MAP:
            if filename not in names_in_zip:
                logger.warning(f"  {filename} not in zip — skipped")
                continue

            cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))

            with zf.open(filename) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                raw_rows = [[row.get(c) for c in csv_cols] for row in reader]

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

    # Commit once, after every table — a mid-load failure then rolls back the
    # whole static set rather than leaving a partial one (which `_static_loaded`,
    # gated only on static_stops, would treat as a complete load).
    conn.commit()

    logger.info("Static data loaded.")
