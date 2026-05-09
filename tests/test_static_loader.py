# tests/test_static_loader.py
import io
import zipfile

from pipeline.static_loader import load_static


def _make_zip(tmp_path, stops_rows=None, trips_rows=None, routes_rows=None):
    """Build a minimal GTFS Static zip for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if stops_rows is not None:
            content = "stop_id,stop_name,stop_lat,stop_lon\n" + "\n".join(stops_rows)
            zf.writestr("stops.txt", content)
        if trips_rows is not None:
            content = "trip_id,route_id,trip_headsign,shape_id\n" + "\n".join(trips_rows)
            zf.writestr("trips.txt", content)
        if routes_rows is not None:
            content = "route_id,route_short_name\n" + "\n".join(routes_rows)
            zf.writestr("routes.txt", content)
    zip_path = tmp_path / "test_static.zip"
    zip_path.write_bytes(buf.getvalue())
    return str(zip_path)


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


def test_load_static_zip_without_shapes_succeeds(pg_conn, agency_id, capsys):
    """A static zip lacking shapes.txt must still load other tables and log a skip."""
    from pipeline.static_loader import load_static

    load_static("tests/fixtures/static_no_shapes.zip", agency_id, pg_conn)
    out = capsys.readouterr().out
    assert "shapes.txt not in zip — skipped" in out

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM static_shapes WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 0
