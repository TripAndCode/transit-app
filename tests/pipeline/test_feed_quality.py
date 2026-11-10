"""analyze builds each agency's monthly feed-quality scorecard from the raw
feed rows, and its completeness and clamp share are the board's.

April 2026 starts on a Wednesday. The scorecard's clock is frozen on 2 May, so
April is closed and May has one closed day; a month counts its days only as far
as some agency's aggregates reach.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import feed_quality
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.reports.comparability import assess, clamp_pct
from pipeline.reports.network import compute_network_summary
from pipeline.reports.service_delivered import compute_service_delivered_by_agency
from tests.fixtures.clock import freeze_now, jst_midday

_APRIL = date(2026, 4, 1)
_MAY = date(2026, 5, 1)
_POLL = datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc)  # 10:00 JST
_TRIPS = [f"ALL-{i}" for i in range(4)]
# The four polls' ages: tied below the median, so the quantile rule shows.
_AGES = (10, 10, 10, 40)


def _agency(pg_conn, name: str, strategy: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES (%s, %s, %s) RETURNING agency_id",
            (name, f"http://{name}", strategy),
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    return agency_id


def _timetable(pg_conn, agency_id: int, stops: int, last_day: str = "2026-04-30") -> None:
    """Every trip of _TRIPS runs every day from 1 April to *last_day*, calling at *stops* stops."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
            "saturday, sunday, start_date, end_date) VALUES (%s, 'ALL', true, true, true, true, true, true, true, "
            "'2026-04-01', %s)",
            (agency_id, last_day),
        )
        for trip in _TRIPS:
            cur.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, 'R1', 'ALL')",
                (agency_id, trip),
            )
            for seq in range(1, stops + 1):
                cur.execute(
                    "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
                    "VALUES (%s, %s, %s, %s, '11:00:00')",
                    (agency_id, trip, seq, f"S{seq}"),
                )
    pg_conn.commit()


def _observed_elsewhere(pg_conn, ch_client, day: date) -> int:
    """Another feed, whose trips are not the timetable's, observed on *day*:
    the aggregates reach that day for some agency, as the board's coverage reads."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT agency_id FROM agencies WHERE agency_name = 'unmatched'")
        row = cur.fetchone()
    agency_id = row[0] if row else _agency(pg_conn, "unmatched", "aomori_regex")
    insert_updates(
        ch_client,
        agency_id,
        [(f"{day:%Y%m%d}/TripUpdate_030000.pb", jst_midday(day), "T1", "平日", "11:00:00", "R9", 1, 60)],
    )
    analyze(agency_id, pg_conn, ch_client)
    return agency_id


def _row(file_name: str, captured_at: datetime, trip: str, seq: int, dep: int | None, age: int | None):
    """One stop_time_update a static_join feed stores: every RT coverage field
    present, the poll's header *age* seconds before its capture."""
    header = None if age is None else int(captured_at.timestamp()) - age
    return (file_name, captured_at, trip, "平日", "11:00:00", "R1", seq, dep, f"S{seq}", dep, 0, 0, header)


def _live_polls(day_start: datetime = _POLL) -> list[tuple]:
    """Four live polls of every trip at both stops; the last reports one
    implausible delay."""
    rows = []
    for i, age in enumerate(_AGES):
        captured = day_start + timedelta(minutes=i)
        name = f"oracle/{captured:%Y%m%d}/TripUpdate_{captured:%H%M%S}.pb"
        for trip in _TRIPS:
            for seq in (1, 2):
                dep = 9000 if (i == 3 and trip == "ALL-0" and seq == 1) else 30
                rows.append(_row(name, captured, trip, seq, dep, age))
    return rows


def _stored(pg_conn, agency_id: int) -> dict[date, dict]:
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {feed_quality.TABLE} WHERE agency_id = %s ORDER BY month", (agency_id,))
        names = [c.name for c in cur.description]
        return {row[1]: dict(zip(names, row, strict=True)) for row in cur.fetchall()}


@pytest.fixture
def closed_on_may_first(monkeypatch):
    freeze_now(monkeypatch, feed_quality, jst_midday(date(2026, 5, 2)))


async def _board(aconn, ch_async_client, first: date, last: date) -> dict[int, dict]:
    compute_network_summary.cache_clear()
    return {row["agency_id"]: row for row in await compute_network_summary(aconn, ch_async_client, first, last)}


@pytest.mark.asyncio
async def test_a_closed_month_reads_what_the_board_reads(
    pg_conn, aconn, ch_client, ch_async_client, closed_on_may_first
):
    unmatched = _observed_elsewhere(pg_conn, ch_client, date(2026, 4, 30))
    joined = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, joined, stops=2)
    insert_updates(ch_client, joined, _live_polls())
    analyze(joined, pg_conn, ch_client)

    april = _stored(pg_conn, joined)[_APRIL]
    assert (april["through_date"], april["covered_days"], april["window_days"]) == (date(2026, 4, 30), 30, 30)
    # 4 trips a day for 30 days planned, the 4 reported on the 1st observed.
    assert (april["observed_trips"], april["planned_trips"], float(april["rt_completeness_pct"])) == (4, 120, 3.3)
    # 2 stops of each: 8 stop events reported of 240 planned.
    assert (april["observed_stop_events"], april["planned_stop_events"], april["stop_plan_days"]) == (8, 240, 30)
    assert float(april["stop_completeness_pct"]) == 3.3
    # 1 of 32 is 3.125%, which the gate's clamp_pct rounds as the board does.
    assert (april["raw_samples"], april["clamp_count"], float(april["clamp_pct"])) == (32, 1, clamp_pct(1, 32))
    assert (april["message_age_polls"], april["message_age_p50_sec"], april["message_age_p95_sec"]) == (4, 10.0, 35.5)
    assert april["stop_time_updates"] == 32
    assert all(float(april[f"{field}_pct"]) == 100.0 for field in ("stop_id", "arr_delay"))
    assert april["vehicle_positions_missing"] == "not_recorded"
    # The aggregates reach no day of May yet, for any agency.
    assert _MAY not in _stored(pg_conn, joined)

    delivered = (await compute_service_delivered_by_agency(aconn, [joined], _APRIL, date(2026, 4, 30)))[joined]
    gate = assess(
        ingest_strategy="static_join",
        observed_trips=delivered["observed_trips"],
        coverage_planned_trips=delivered["coverage_planned_trips"],
        on_time_count=0,
        samples=1,
        clamp_pct=None,
    )
    assert float(april["rt_completeness_pct"]) == gate.coverage_pct
    board = await _board(aconn, ch_async_client, _APRIL, date(2026, 4, 30))
    assert float(april["rt_completeness_pct"]) == board[joined]["coverage_pct"]
    assert float(april["clamp_pct"]) == board[joined]["clamp_pct"]

    other = _stored(pg_conn, unmatched)[_APRIL]
    assert (other["rt_completeness_pct"], other["rt_completeness_missing"]) == (None, "coverage_unknown")
    assert (other["stop_completeness_pct"], other["stop_completeness_missing"]) == (None, "coverage_unknown")
    assert other["field_coverage_missing"] == "feed_cannot_report"
    # Archive ingest stamps captured_at from the header itself.
    assert (other["message_age_p50_sec"], other["message_age_missing"]) == (None, "archive_stamped")


@pytest.mark.asyncio
async def test_the_month_in_progress_reads_what_the_board_reads_through_its_newest_day(
    pg_conn, aconn, ch_client, ch_async_client, closed_on_may_first
):
    _observed_elsewhere(pg_conn, ch_client, _MAY)
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2, last_day="2026-05-31")
    insert_updates(ch_client, agency, _live_polls())
    analyze(agency, pg_conn, ch_client)

    may = _stored(pg_conn, agency)[_MAY]
    # May's one closed day plans 4 trips this feed never reported.
    assert (may["through_date"], may["window_days"], may["covered_days"]) == (_MAY, 1, 1)
    assert (may["observed_trips"], may["planned_trips"], float(may["rt_completeness_pct"])) == (0, 4, 0.0)
    board = await _board(aconn, ch_async_client, _MAY, may["through_date"])
    assert float(may["rt_completeness_pct"]) == board[agency]["coverage_pct"]
    assert (may["message_age_missing"], may["clamp_missing"], may["field_coverage_missing"]) == (
        "no_data",
        "no_data",
        "no_data",
    )


def test_a_month_counts_only_through_the_newest_day_the_aggregates_reach(pg_conn, ch_client, closed_on_may_first):
    """The clock has closed May's first day, but no agency's aggregates reach
    past 10 April: a day none of them has reached is a lag they share, not
    trips this feed missed."""
    _observed_elsewhere(pg_conn, ch_client, date(2026, 4, 10))
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2)
    insert_updates(ch_client, agency, _live_polls())
    analyze(agency, pg_conn, ch_client)
    april = _stored(pg_conn, agency)[_APRIL]
    assert (april["through_date"], april["planned_trips"]) == (date(2026, 4, 10), 40)

    _observed_elsewhere(pg_conn, ch_client, date(2026, 4, 20))
    analyze(agency, pg_conn, ch_client)

    # No row of this feed arrived, yet the ten days the aggregates have since
    # reached count against it.
    april = _stored(pg_conn, agency)[_APRIL]
    assert (april["through_date"], april["observed_trips"], april["planned_trips"]) == (date(2026, 4, 20), 4, 80)


def test_a_day_saved_without_its_stop_events_is_left_out_of_the_stop_ratio(pg_conn, ch_client, closed_on_may_first):
    _observed_elsewhere(pg_conn, ch_client, date(2026, 4, 30))
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips) VALUES (%s, '2026-04-01', 4)",
            (agency,),
        )
    pg_conn.commit()
    insert_updates(ch_client, agency, _live_polls())
    analyze(agency, pg_conn, ch_client)

    april = _stored(pg_conn, agency)[_APRIL]
    # The 1st's stop events are unknown, so its 8 observed ones go with it.
    assert (april["observed_stop_events"], april["planned_stop_events"], april["stop_plan_days"]) == (0, 232, 29)
    assert april["planned_trips"] == 120


def test_a_deleted_agencys_later_days_do_not_stretch_a_live_agencys_window(pg_conn, ch_client, closed_on_may_first):
    """The board lists only agencies not deleted, so only their aggregates say
    how far coverage reaches."""
    gone = _observed_elsewhere(pg_conn, ch_client, date(2026, 4, 30))
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = %s", (gone,))
    pg_conn.commit()
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2)
    insert_updates(ch_client, agency, _live_polls())
    analyze(agency, pg_conn, ch_client)

    april = _stored(pg_conn, agency)[_APRIL]
    assert (april["through_date"], april["planned_trips"]) == (_APRIL, 4)


def _nodes(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def test_the_newest_day_is_read_per_agency_off_the_index(pg_conn):
    """Each live agency's newest day is an index probe on the (agency_id, date)
    key, never a scan of every agency's rows."""
    with pg_conn.cursor() as cur:
        cur.execute("SET LOCAL enable_seqscan = off")
        cur.execute("EXPLAIN (FORMAT JSON) " + feed_quality.NEWEST_DAY_SQL)
        plan = cur.fetchone()[0][0]["Plan"]
    pg_conn.rollback()
    scans = [node for node in _nodes(plan) if node.get("Relation Name") == "agg_route_daily_dist"]
    assert scans

    def keyed(node: dict) -> bool:
        conditions = (n.get("Index Cond", "") + n.get("Recheck Cond", "") for n in _nodes(node))
        return any("agency_id" in condition for condition in conditions)

    assert all(keyed(node) for node in scans), scans


def _one_poll(withheld: tuple) -> list[tuple]:
    """One live poll of every trip at both stops, the stop *withheld* names
    reported as the rows of the given (dep_delay, arr_delay, stop status)."""
    captured = _POLL
    name = f"oracle/{captured:%Y%m%d}/TripUpdate_{captured:%H%M%S}.pb"
    header = int(captured.timestamp()) - 10
    rows = []
    for trip in _TRIPS:
        for seq in (1, 2):
            dep, arr, stop_status = (30, 30, 0) if (trip, seq) != withheld[:2] else withheld[2:]
            rows.append(
                (name, captured, trip, "平日", "11:00:00", "R1", seq, dep, f"S{seq}", arr, 0, stop_status, header)
            )
    return rows


@pytest.mark.parametrize(
    ("report", "reported"),
    [
        # SKIPPED with no time: the feed said something about the stop.
        ((None, None, 1), 8),
        # Only an implausible delay: the clamp drops it, as everywhere else.
        ((9000, None, 0), 7),
    ],
    ids=["skipped", "implausible"],
)
def test_a_stop_event_counts_as_reported_on_a_plausible_delay_or_a_withdrawal(
    pg_conn, ch_client, closed_on_may_first, report, reported
):
    _observed_elsewhere(pg_conn, ch_client, date(2026, 4, 30))
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2)
    insert_updates(ch_client, agency, _one_poll(("ALL-3", 2, *report)))
    analyze(agency, pg_conn, ch_client)

    assert _stored(pg_conn, agency)[_APRIL]["observed_stop_events"] == reported


def test_a_trip_cancelled_without_listing_a_stop_reports_its_stops_but_sends_no_stop_update(
    pg_conn, ch_client, closed_on_may_first
):
    """Ingest writes a cancellation that lists no stop as a row per stop the
    timetable schedules. Those rows report the trip's two stop events, as a
    withdrawal does, but the feed sent no update for them, so field coverage
    and the update count read only the six updates it did send."""
    from pipeline.strategies import static_join
    from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed

    _observed_elsewhere(pg_conn, ch_client, date(2026, 4, 30))
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2)
    sent = (
        trip_update(
            trip,
            *(
                stop_time_update(seq, departure_delay=30, arrival_delay=30, stop_id=f"S{seq}", schedule_relationship=0)
                for seq in (1, 2)
            ),
            route_id="R1",
            schedule_relationship=0,
        )
        for trip in _TRIPS[:3]
    )
    pb = trip_update_feed(*sent, trip_update(_TRIPS[3], schedule_relationship=3), timestamp=int(_POLL.timestamp()) - 10)
    name = f"oracle/{_POLL:%Y%m%d}/TripUpdate_{_POLL:%H%M%S}.pb"
    rows = static_join.parse_feed(pb, _POLL.isoformat(), name, agency, pg_conn)
    assert len(rows) == 8
    insert_updates(ch_client, agency, rows)
    analyze(agency, pg_conn, ch_client)

    april = _stored(pg_conn, agency)[_APRIL]
    assert (april["observed_stop_events"], april["stop_time_updates"]) == (8, 6)
    assert {field: float(april[f"{field}_pct"]) for field in static_join.RT_COVERAGE_FIELDS} == dict.fromkeys(
        static_join.RT_COVERAGE_FIELDS, 100.0
    )
