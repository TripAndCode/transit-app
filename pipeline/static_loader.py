import csv
import io
import zipfile
import pathlib
import psycopg2.extras

_STATIC_FILE_MAP = [
    ("stops.txt",          "static_stops",
     ["stop_id", "stop_name", "stop_lat", "stop_lon"]),
    ("stop_times.txt",     "static_stop_times",
     ["trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"]),
    ("trips.txt",          "static_trips",
     ["trip_id", "route_id", "trip_headsign", "shape_id"]),
    ("routes.txt",         "static_routes",
     ["route_id", "route_short_name"]),
    ("calendar_dates.txt", "static_calendar_dates",
     ["service_id", "date", "exception_type"]),
]

_DB_COLS = {
    "static_stops":          ["agency_id", "stop_id", "stop_name", "stop_lat", "stop_lon"],
    "static_stop_times":     ["agency_id", "trip_id", "stop_sequence", "stop_id",
                               "arrival_time", "departure_time"],
    "static_trips":          ["agency_id", "trip_id", "route_id", "trip_headsign", "shape_id"],
    "static_routes":         ["agency_id", "route_id", "route_short_name"],
    "static_calendar_dates": ["agency_id", "service_id", "date", "exception_type"],
}


def load_static(path: str, agency_id: int, conn) -> None:
    p = pathlib.Path(path)
    if p.is_dir():
        zips = sorted(p.glob("*_static.zip"))
        if not zips:
            print(f"No *_static.zip found in {p}"); return
        p = zips[-1]
        print(f"Using: {p.name}")
    if not p.exists():
        print(f"File not found: {p}"); return

    with zipfile.ZipFile(p) as zf, conn.cursor() as cur:
        names_in_zip = set(zf.namelist())
        for filename, table, csv_cols in _STATIC_FILE_MAP:
            if filename not in names_in_zip:
                print(f"  {filename} not in zip — skipped")
                continue

            cur.execute(
                f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,)
            )

            with zf.open(filename) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                raw_rows = [[row.get(c) for c in csv_cols] for row in reader]

            if table == "static_stops":
                for row in raw_rows:
                    stop_id, stop_name = row[0], row[1]
                    try:
                        lat, lon = float(row[2]), float(row[3])
                        geom_sql = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"
                        params = [agency_id, stop_id, stop_name, lat, lon, lon, lat]
                    except (TypeError, ValueError):
                        geom_sql = "NULL"
                        params = [agency_id, stop_id, stop_name, None, None]
                    cur.execute(
                        f"INSERT INTO static_stops "
                        f"(agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
                        f"VALUES (%s, %s, %s, %s, %s, {geom_sql}) "
                        f"ON CONFLICT (agency_id, stop_id) DO UPDATE SET "
                        f"stop_name=EXCLUDED.stop_name, stop_lat=EXCLUDED.stop_lat, "
                        f"stop_lon=EXCLUDED.stop_lon, geom=EXCLUDED.geom",
                        params,
                    )
            else:
                db_cols = _DB_COLS[table]
                col_list = ", ".join(db_cols)
                placeholders = ", ".join(["%s"] * len(db_cols))
                pg_rows = [[agency_id] + row for row in raw_rows]
                psycopg2.extras.execute_batch(
                    cur,
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT DO NOTHING",
                    pg_rows,
                )

            conn.commit()
            print(f"  {table}: {len(raw_rows):,} rows")

    print("Static data loaded.")
