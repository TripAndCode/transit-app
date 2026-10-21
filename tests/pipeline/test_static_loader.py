"""Integration tests for pipeline.static_loader against a live Postgres."""

import pathlib
import zipfile
from datetime import date

from pipeline.static_loader import load_static
from tests.fixtures.synthetic_gtfs import build_static_zip as _make_zip


def test_load_static_inserts_stops(pg_conn, agency_id, tmp_path):
    zip_path = _make_zip(
        tmp_path,
        stops_rows=["S1,青森駅,40.8244,140.7400", "S2,市役所前,40.8201,140.7368"],
    )
    load_static(zip_path, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_id, stop_name FROM static_stops WHERE agency_id = %s ORDER BY stop_id",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0] == ("S1", "青森駅")


def test_load_static_sets_geom(pg_conn, agency_id, tmp_path):
    zip_path = _make_zip(
        tmp_path,
        stops_rows=["S1,青森駅,40.8244,140.7400"],
    )
    load_static(zip_path, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ST_AsText(geom) FROM static_stops WHERE agency_id = %s AND stop_id = 'S1'",
            (agency_id,),
        )
        geom_text = cur.fetchone()[0]
    assert "POINT" in geom_text


def test_load_static_inserts_routes(pg_conn, agency_id, tmp_path):
    zip_path = _make_zip(
        tmp_path,
        routes_rows=["国道・古川線(44372),A1 国道・古川線"],
    )
    load_static(zip_path, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_id, route_short_name FROM static_routes WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row == ("国道・古川線(44372)", "A1 国道・古川線")


def test_load_static_agency_isolated(pg_conn, tmp_path):
    """Two agencies loading the same stop_id don't conflict."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency A", "http://a.example.com"),
        )
        aid_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency B", "http://b.example.com"),
        )
        aid_b = cur.fetchone()[0]
    pg_conn.commit()

    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    zip_a = _make_zip(dir_a, stops_rows=["S1,駅A,40.0,140.0"])
    zip_b = _make_zip(dir_b, stops_rows=["S1,駅B,41.0,141.0"])

    load_static(zip_a, aid_a, pg_conn)
    load_static(zip_b, aid_b, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_name FROM static_stops WHERE agency_id = %s AND stop_id = 'S1'",
            (aid_a,),
        )
        assert cur.fetchone()[0] == "駅A"
        cur.execute(
            "SELECT stop_name FROM static_stops WHERE agency_id = %s AND stop_id = 'S1'",
            (aid_b,),
        )
        assert cur.fetchone()[0] == "駅B"


def test_load_static_shapes_builds_linestrings(pg_conn, agency_id):
    from pipeline.static_loader import load_static

    load_static("tests/fixtures/static_with_shapes.zip", agency_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT shape_id, ST_AsText(geom), ST_NumPoints(geom) "
            "FROM static_shapes WHERE agency_id = %s ORDER BY shape_id",
            (agency_id,),
        )
        rows = cur.fetchall()

    assert [r[0] for r in rows] == ["S1", "S2", "S3"]
    assert [r[2] for r in rows] == [3, 3, 2]
    # First point of S1 = (lon=140.7400, lat=40.8200)
    assert rows[0][1].startswith("LINESTRING(140.74 40.82,")


def test_load_static_shapes_two_loads_no_duplicates(pg_conn, agency_id):
    from pipeline.static_loader import load_static

    load_static("tests/fixtures/static_with_shapes.zip", agency_id, pg_conn)
    load_static("tests/fixtures/static_with_shapes.zip", agency_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM static_shapes WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 3, "second load must not produce duplicate rows"


def test_load_static_zip_without_shapes_succeeds(pg_conn, agency_id, caplog):
    """A static zip lacking shapes.txt must still load other tables and log a skip."""
    from pipeline.static_loader import load_static

    with caplog.at_level("WARNING", logger="pipeline.static_loader"):
        load_static("tests/fixtures/static_no_shapes.zip", agency_id, pg_conn)
    assert "shapes.txt not in zip — skipped" in caplog.text

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM static_shapes WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 0


def test_load_static_populates_service_id_for_hiroden(pg_conn, tmp_path):
    """Hiroden's trips.txt has service_id; loader must populate the new column."""
    zip_path = pathlib.Path(__file__).parent.parent / "fixtures" / "hiroden_static.zip"
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("広島電鉄_loader_test", "http://hiroden-loader-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()
    load_static(str(zip_path), aid, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM static_trips WHERE agency_id = %s AND service_id IS NOT NULL",
            (aid,),
        )
        with_sid = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM static_trips WHERE agency_id = %s",
            (aid,),
        )
        total = cur.fetchone()[0]
    assert total > 0, "no trips loaded"
    # Hiroden has service_id on every trip
    assert with_sid == total, f"only {with_sid}/{total} trips have service_id"


def test_load_static_sets_static_version_id_from_zip_stem(pg_conn, agency_id, tmp_path):
    """static_trips.static_version_id must be the loaded zip's filename stem,
    the same value for every row loaded in one load_static() call — the only
    identifier that survives past this function to mark which static feed
    version an RT row's static_join later matched against."""
    zip_path = _make_zip(
        tmp_path,
        trips_rows=["T1,R1,,", "T2,R1,,"],
        filename="gtfs_static_20260101.zip",
    )
    load_static(zip_path, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT trip_id, static_version_id FROM static_trips WHERE agency_id = %s ORDER BY trip_id",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert rows == [("T1", "gtfs_static_20260101"), ("T2", "gtfs_static_20260101")]


def test_load_static_updates_static_version_id_on_reload(pg_conn, agency_id, tmp_path):
    """A later load_static() call with a differently-named zip must replace
    the previous static_version_id, not leave the stale value behind —
    matching this function's existing per-table DELETE-then-insert
    idempotency for every other static_trips column."""
    zip_path_1 = _make_zip(tmp_path, trips_rows=["T1,R1,,"], filename="gtfs_static_20260101.zip")
    load_static(zip_path_1, agency_id, pg_conn)
    zip_path_2 = _make_zip(tmp_path, trips_rows=["T1,R1,,"], filename="gtfs_static_20260201.zip")
    load_static(zip_path_2, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT static_version_id FROM static_trips WHERE agency_id = %s AND trip_id = 'T1'",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert rows == [("gtfs_static_20260201",)]


def test_load_static_builds_stop_clusters_for_same_named_nearby_platforms(pg_conn, agency_id, tmp_path):
    """Two platforms of one named stop ~170 m apart share a cluster; a
    same-named stop ~840 m away and an unnamed stop each stand alone."""
    zip_path = _make_zip(
        tmp_path,
        stops_rows=[
            "SA,境界前,40.0,139.974",
            "SB,境界前,40.0,139.976",
            "SC,境界前,40.0,139.985",
            "SU,,40.5,140.5",
            "SN,無座標,,",
        ],
    )
    load_static(zip_path, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_id, name_key, cluster_id FROM stop_clusters WHERE agency_id = %s ORDER BY stop_id",
            (agency_id,),
        )
        rows = {stop_id: (name_key, cluster_id) for stop_id, name_key, cluster_id in cur.fetchall()}
    assert set(rows) == {"SA", "SB", "SC", "SU"}, "a stop without geometry has no cluster row"
    assert rows["SA"] == rows["SB"]
    assert rows["SC"][0] == "境界前" and rows["SC"][1] != rows["SA"][1]
    assert rows["SU"][0] == "unnamed:SU"


def test_load_static_replaces_stop_clusters_on_reload(pg_conn, agency_id, tmp_path):
    load_static(_make_zip(tmp_path, stops_rows=["S1,駅前,40.0,140.0", "S2,駅前,40.0,140.001"]), agency_id, pg_conn)
    second = _make_zip(tmp_path, stops_rows=["S1,駅前,40.0,140.0"], filename="second_static.zip")
    load_static(second, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT stop_id FROM stop_clusters WHERE agency_id = %s", (agency_id,))
        assert [r[0] for r in cur.fetchall()] == ["S1"]


def test_load_static_loads_calendar_weekly_flags_and_range(pg_conn, agency_id):
    """A real feed's calendar.txt lands in static_calendar typed (booleans and
    dates), with each service's trips.txt trip count."""
    zip_path = str(pathlib.Path(__file__).parent.parent / "fixtures" / "hiroden_static.zip")
    load_static(zip_path, agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM static_calendar WHERE agency_id = %s", (agency_id,))
        count = cur.fetchone()[0]
        cur.execute(
            "SELECT monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date, trips "
            "FROM static_calendar WHERE agency_id = %s AND service_id = %s",
            (agency_id, "11_土曜(共通)"),
        )
        saturday_service = cur.fetchone()
    assert count == 45
    assert saturday_service == (
        *(False, False, False, False, False, True, False),
        date(2026, 4, 8),
        date(2026, 10, 8),
        219,
    )


def test_load_static_skips_malformed_calendar_rows(pg_conn, agency_id, tmp_path):
    """One unparseable calendar.txt row is dropped, not allowed to abort the
    whole static load, and a reload replaces rather than duplicates the rest."""
    zip_path = tmp_path / "calendar_static.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nS1,A,40.8,140.7")
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "WD,1,1,1,1,1,0,0,20260401,20270331\n"
            "BAD_FLAG,1,1,1,1,1,0,yes,20260401,20270331\n"
            "BAD_DATE,0,0,0,0,0,1,1,2026-04-01,20270331\n",
        )
    load_static(str(zip_path), agency_id, pg_conn)
    load_static(str(zip_path), agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT service_id FROM static_calendar WHERE agency_id = %s", (agency_id,))
        loaded = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM static_stops WHERE agency_id = %s", (agency_id,))
        stops = cur.fetchone()[0]
    assert loaded == ["WD"]
    assert stops == 1


def test_load_static_clears_the_calendar_when_a_feed_drops_calendar_txt(pg_conn, agency_id, tmp_path):
    """A feed without calendar.txt leaves no earlier feed's calendar behind to
    file its dates under the old timetables."""
    with_calendar = tmp_path / "with_calendar_static.zip"
    with zipfile.ZipFile(with_calendar, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,trip_headsign,shape_id,service_id\nT1,R1,,,WD\nT2,R1,,,WD")
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "WD,1,1,1,1,1,0,0,20260401,20270331\n"
            "SAT,0,0,0,0,0,1,0,20260401,20270331\n",
        )
    load_static(str(with_calendar), agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT service_id, trips FROM static_calendar WHERE agency_id = %s ORDER BY service_id", (agency_id,)
        )
        assert cur.fetchall() == [("SAT", 0), ("WD", 2)]

    without_calendar = tmp_path / "without_calendar_static.zip"
    with zipfile.ZipFile(without_calendar, "w") as zf:
        zf.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nS1,A,40.8,140.7")
    load_static(str(without_calendar), agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM static_calendar WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 0


def test_load_static_clears_calendar_dates_when_a_feed_drops_calendar_dates_txt(pg_conn, agency_id, tmp_path):
    """A feed without calendar_dates.txt leaves no earlier feed's exceptions
    behind to add or remove a reused service_id on this feed's dates."""
    calendar = (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WD,1,1,1,1,1,0,0,20260401,20270331\n"
    )
    with_dates = tmp_path / "with_dates_static.zip"
    with zipfile.ZipFile(with_dates, "w") as zf:
        zf.writestr("calendar.txt", calendar)
        zf.writestr("calendar_dates.txt", "service_id,date,exception_type\nWD,20260429,2\nWD,20260502,1\n")
    load_static(str(with_dates), agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM static_calendar_dates WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 2

    without_dates = tmp_path / "without_dates_static.zip"
    with zipfile.ZipFile(without_dates, "w") as zf:
        zf.writestr("calendar.txt", calendar)
    load_static(str(without_dates), agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM static_calendar_dates WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 0
