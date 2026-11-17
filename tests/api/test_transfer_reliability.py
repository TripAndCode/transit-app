"""The transfer-pair reads: the pairs an agency takes part in, and a pair's
reliability from the loaded timetables and both operators' predictions in
ClickHouse, each connection filed under its service day."""

import csv
import io
from datetime import timedelta

import pytest

from api.range import jst_today
from pipeline.day_types import fallback_day_type
from pipeline.reports.definition import METHODOLOGY_URLS
from pipeline.reports.transfers import days_of_type
from pipeline.service_day import service_day_moment_utc

DAY = jst_today() - timedelta(days=7)
DAY_TYPE = fallback_day_type(DAY)
RUNS_FROM = DAY


def _agency(pg_conn, name: str, feed_url: str, *, arrivals_confirmed: bool = False) -> int:
    """A static_join agency; *arrivals_confirmed* records the realtime
    field-coverage probe that lets a reader trust its arrival delays."""
    from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES (%s, %s, 'static_join')"
            " RETURNING agency_id",
            (name, feed_url),
        )
        agency_id = cur.fetchone()[0]
        if arrivals_confirmed:
            for field in RT_COVERAGE_FIELDS:
                cur.execute(
                    "INSERT INTO rt_field_coverage_probes (agency_id, field_name, confirmed, source_feed)"
                    " VALUES (%s, %s, true, 'test://probe')",
                    (agency_id, field),
                )
    pg_conn.commit()
    return agency_id


def _shift(clock: str, minutes: int) -> str:
    sec = _sec(clock) + minutes * 60
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:00"


def _trip(pg_conn, agency_id: int, route_id: str, trip_id: str, stop_id: str, clock: str, *, shape: str):
    """One trip of the loaded timetable calling at ``stop_id`` as sequence 4
    at ``clock``, under a service running every day from RUNS_FROM. *shape*
    places the hub on the trip: ``through`` with a stop before and after it,
    ``ends`` as its last stop, ``starts`` as its first."""
    service_id = f"SVC-{trip_id}"
    stops = [(4, stop_id, clock)]
    if shape in ("through", "ends"):
        stops.insert(0, (3, f"{stop_id}-up", _shift(clock, -5)))
    if shape in ("through", "starts"):
        stops.append((5, f"{stop_id}-down", _shift(clock, 5)))
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday,"
            " saturday, sunday, start_date, end_date)"
            " VALUES (%s, %s, true, true, true, true, true, true, true, %s, %s)",
            (agency_id, service_id, RUNS_FROM, RUNS_FROM + timedelta(days=60)),
        )
        cur.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (agency_id, route_id, route_id),
        )
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, %s, %s)",
            (agency_id, trip_id, route_id, service_id),
        )
        for seq, stop, at in stops:
            cur.execute(
                "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, %s, '温品四丁目')"
                " ON CONFLICT DO NOTHING",
                (agency_id, stop),
            )
            cur.execute(
                "INSERT INTO static_stop_times"
                " (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (agency_id, trip_id, seq, stop, at, at),
            )
    pg_conn.commit()


def _sec(clock: str) -> int:
    h, m, s = (int(x) for x in clock.split(":"))
    return h * 3600 + m * 60 + s


def _reading(route: str, trip_id: str, stop_id: str | None, clock: str, *, dep=None, arr=None, day=DAY):
    """One poll of a trip's stop sequence 4, five minutes before its
    scheduled time on service day ``day``; a clock past 24:00 is captured
    after midnight, on the next calendar day, as the feed sends it."""
    sec = _sec(clock)
    captured = service_day_moment_utc(day, sec) - timedelta(minutes=5)
    return (
        f"test/{route}/{trip_id}/{clock}/{'a' if arr is not None else 'd'}.pb",
        captured,
        trip_id,
        "",
        None if sec >= 86400 else clock,
        route,
        4,
        dep,
        stop_id,
        arr,
        None,
        None,
        None,
        sec,
    )


@pytest.fixture
def transfer_app(ch_async_client):
    from api.main import app

    app.state.ch_client = ch_async_client
    return app


@pytest.fixture(params=["stop_id_sent", "stop_id_missing"])
def hub(request, pg_conn, ch_client):
    """Route 29 arrives at H-A and route 2 leaves H-B. The feed sends each
    reading's stop id, or (an optional realtime field) leaves it out."""
    from pipeline.clickhouse import insert_updates

    a_stop = "H-A" if request.param == "stop_id_sent" else None
    b_stop = "H-B" if request.param == "stop_id_sent" else None
    bus = _agency(
        pg_conn, "広島バス", "https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin", arrivals_confirmed=True
    )
    tram = _agency(pg_conn, "広島電鉄", "https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin")
    for trip, clock in [("a1", "10:00:00"), ("a2", "11:00:00"), ("a4", "12:00:00"), ("a3", "24:20:00")]:
        _trip(pg_conn, bus, "29", trip, "H-A", clock, shape="through")
    # A trip that starts at the hub brings nobody to it.
    _trip(pg_conn, bus, "29", "a-start", "H-A", "13:00:00", shape="starts")
    for trip, clock in [
        ("b1", "10:05:00"),
        ("b2", "10:20:00"),
        ("b3", "11:05:00"),
        ("b4", "12:05:00"),
        ("b5", "24:35:00"),
    ]:
        _trip(pg_conn, tram, "2", trip, "H-B", clock, shape="through")
    # A trip that ends at the hub takes nobody on; it reports no departure.
    _trip(pg_conn, tram, "2", "b-end", "H-B", "11:30:00", shape="ends")
    insert_updates(
        ch_client,
        bus,
        [
            _reading("29", "a1", a_stop, "10:00:00", arr=60),
            _reading("29", "a2", a_stop, "11:00:00", arr=300),
            _reading("29", "a3", a_stop, "24:20:00", arr=0),
            _reading("29", "a-start", a_stop, "13:00:00", dep=0),
            # Another trip's poll at the hub is not route 29's arrival.
            _reading("99", "z9", a_stop, "12:00:00", arr=0),
        ],
    )
    insert_updates(
        ch_client,
        tram,
        [
            _reading("2", "b1", b_stop, "10:05:00", dep=0),
            _reading("2", "b2", b_stop, "10:20:00", dep=0),
            _reading("2", "b3", b_stop, "11:05:00", dep=0),
            _reading("2", "b4", b_stop, "12:05:00", dep=0),
            _reading("2", "b5", b_stop, "24:35:00", dep=0),
            _reading("2", "b-end", b_stop, "11:30:00", arr=0),
        ],
    )
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO transfer_pairs (name, from_agency_id, from_route_code, from_stop_ids, to_agency_id,"
            " to_route_code, to_stop_ids, walk_sec)"
            " VALUES ('温品四丁目 29→2', %s, '29', '{H-A}', %s, '2', '{H-B}', 120)"
            " RETURNING id",
            (bus, tram),
        )
        pair_id = cur.fetchone()[0]
    pg_conn.commit()
    return bus, tram, pair_id


def _url(agency: int, pair_id: int, **params) -> str:
    query = {"from": DAY.isoformat(), "to": DAY.isoformat(), "day_type": DAY_TYPE, **params}
    return f"/api/{agency}/transfer-pairs/{pair_id}/reliability?" + "&".join(f"{k}={v}" for k, v in query.items())


@pytest.mark.asyncio
async def test_a_pair_reads_held_missed_and_unobserved_on_its_service_day(client, hub, transfer_app):
    _bus, tram, pair_id = hub
    resp = await client.get(_url(tram, pair_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    total = body["total"]
    assert (total["planned"], total["held"], total["missed"], total["unobserved"]) == (4, 2, 1, 1)
    assert total["stranded"] == 0
    assert body["no_onward"] == 0
    assert [d["day"] for d in body["by_day"]] == [DAY.isoformat()]
    hours = {h["hour"]: (h["held"], h["missed"], h["unobserved"]) for h in body["by_hour"]}
    assert hours == {10: (1, 0, 0), 11: (0, 1, 0), 12: (0, 0, 1), 24: (1, 0, 0)}
    assert [d["day"] for d in body["worst_days"]] == [DAY.isoformat()]
    assert body["waits"] == 3
    # Below the sample floor, no share, percentile or buffer is given.
    assert body["held_share"] is None and body["buffer_min"] is None and body["wait_p50_sec"] is None
    assert body["definition"]["value_basis"] == "feed_prediction"
    assert body["pair"]["arriving"]["stop_names"] == ["温品四丁目"]


@pytest.mark.asyncio
async def test_trips_that_end_or_start_at_the_hub_plan_no_connection(client, hub, transfer_app):
    bus, _tram, pair_id = hub
    body = (await client.get(_url(bus, pair_id))).json()
    # A trip ending at the hub would be an unobserved departure in a2's
    # forward scan, and one starting there an arrival at 13:00.
    assert 13 not in {h["hour"] for h in body["by_hour"]}
    assert body["total"]["planned"] == 4
    assert body["waits"] == 3


@pytest.mark.asyncio
async def test_arrival_delays_count_only_from_a_feed_confirmed_to_send_them(client, hub, pg_conn, transfer_app):
    bus, _tram, pair_id = hub
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM rt_field_coverage_probes WHERE agency_id = %s", (bus,))
    pg_conn.commit()
    total = (await client.get(_url(bus, pair_id))).json()["total"]
    # Route 29's readings at the hub are arrivals only, so with arrivals
    # unconfirmed no connection has a reading on its arriving side.
    assert (total["planned"], total["held"], total["missed"], total["unobserved"]) == (4, 0, 0, 4)


@pytest.mark.asyncio
async def test_an_after_midnight_connection_files_under_the_evening_it_belongs_to(client, hub, transfer_app):
    bus, _tram, pair_id = hub
    body = (await client.get(_url(bus, pair_id))).json()
    (hour24,) = [h for h in body["by_hour"] if h["hour"] == 24]
    assert (hour24["planned"], hour24["held"], hour24["unobserved"]) == (1, 1, 0)


@pytest.mark.asyncio
async def test_days_the_loaded_timetable_does_not_plan_are_listed_not_counted(client, hub, transfer_app):
    bus, _tram, pair_id = hub
    start = DAY - timedelta(days=13)
    body = (await client.get(_url(bus, pair_id, **{"from": start.isoformat()}))).json()
    assert body["no_plan_days"] == [d.isoformat() for d in days_of_type(start, DAY - timedelta(days=1), DAY_TYPE)]
    assert body["total"]["planned"] == 4


@pytest.mark.asyncio
async def test_the_csv_carries_the_report_preamble_and_a_row_per_day(client, hub, transfer_app):
    bus, _tram, pair_id = hub
    resp = await client.get(_url(bus, pair_id, format="csv", lang="en"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(resp.text.lstrip("﻿"))))
    definition = rows[0][0]
    assert "値の根拠=" in definition and METHODOLOGY_URLS["ja"] in definition and "算出方法=" in definition
    header = [r[0] for r in rows].index("日付")
    assert rows[header + 1][:5] == [DAY.isoformat(), "4", "2", "1", "1"]
    assert any("当日中に乗り継ぐ便が計画されていない到着 0" in r[0] for r in rows[:header])


@pytest.mark.asyncio
async def test_the_csv_writes_a_name_that_opens_like_a_formula_as_text(client, hub, pg_conn, transfer_app):
    bus, _tram, pair_id = hub
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE transfer_pairs SET name = %s WHERE id = %s", ('=HYPERLINK("http://x","y")', pair_id))
        cur.execute("UPDATE agencies SET agency_name = '+cmd' WHERE agency_id = %s", (bus,))
    pg_conn.commit()
    text = (await client.get(_url(bus, pair_id, format="csv"))).text.lstrip("\ufeff")
    assert "'=HYPERLINK" in text and "'+cmd" in text
    rows = list(csv.reader(io.StringIO(text)))
    assert not any(cell.startswith(("=", "+", "-", "@")) for row in rows for cell in row)


@pytest.mark.asyncio
async def test_pairs_are_listed_for_either_agency_while_both_are_live_and_active(client, hub, pg_conn, transfer_app):
    bus, tram, pair_id = hub
    for agency in (bus, tram):
        listed = (await client.get(f"/api/{agency}/transfer-pairs")).json()["pairs"]
        assert [p["id"] for p in listed] == [pair_id]
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = %s", (tram,))
    pg_conn.commit()
    assert (await client.get(f"/api/{bus}/transfer-pairs")).json()["pairs"] == []
    assert (await client.get(_url(bus, pair_id))).status_code == 404


@pytest.mark.asyncio
async def test_an_inactive_pair_or_another_agencys_pair_is_not_read(client, hub, pg_conn, transfer_app):
    bus, _tram, pair_id = hub
    other = _agency(pg_conn, "other", "http://other/rt")
    assert (await client.get(_url(other, pair_id))).status_code == 404
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE transfer_pairs SET active = false WHERE id = %s", (pair_id,))
    pg_conn.commit()
    assert (await client.get(_url(bus, pair_id))).status_code == 404


@pytest.mark.asyncio
async def test_a_day_type_outside_the_pairs_scope_and_bad_windows_are_refused(client, hub, pg_conn, transfer_app):
    bus, _tram, pair_id = hub
    other_type = next(t for t in ("weekday", "saturday", "sunday_holiday") if t != DAY_TYPE)
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE transfer_pairs SET day_types = %s WHERE id = %s", ([DAY_TYPE], pair_id))
    pg_conn.commit()
    assert (await client.get(_url(bus, pair_id, day_type=other_type))).status_code == 422
    assert (await client.get(_url(bus, pair_id, to=jst_today().isoformat()))).status_code == 422
    assert (await client.get(_url(bus, pair_id, **{"from": (DAY + timedelta(days=1)).isoformat()}))).status_code == 422
    far = (DAY - timedelta(days=200)).isoformat()
    assert (await client.get(_url(bus, pair_id, **{"from": far}))).status_code == 422
