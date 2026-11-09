"""analyze builds each agency's monthly feed-quality scorecard from the raw
feed rows, and its completeness and clamp share are the board's.

April 2026 starts on a Wednesday. The scorecard's clock is frozen on 2 May, so
April is closed and May counts its first day only.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import feed_quality
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.reports.comparability import assess
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


def _timetable(pg_conn, agency_id: int, stops: int) -> None:
    """Every trip of _TRIPS runs every day of April, calling at *stops* stops."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
            "saturday, sunday, start_date, end_date) VALUES (%s, 'ALL', true, true, true, true, true, true, true, "
            "'2026-04-01', '2026-04-30')",
            (agency_id,),
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


@pytest.mark.asyncio
async def test_a_closed_month_reads_what_the_board_reads(pg_conn, aconn, ch_client, ch_async_client, closed_on_may_first):
    joined = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, joined, stops=2)
    insert_updates(ch_client, joined, _live_polls())
    analyze(joined, pg_conn, ch_client)
    # A second feed observed on the month's last day, so the board counts
    # April's planned days through its end as the scorecard does.
    unmatched = _agency(pg_conn, "unmatched", "aomori_regex")
    insert_updates(
        ch_client,
        unmatched,
        [("20260430/TripUpdate_030000.pb", jst_midday(date(2026, 4, 30)), "T1", "平日", "11:00:00", "R9", 1, 60)],
    )
    analyze(unmatched, pg_conn, ch_client)

    april = _stored(pg_conn, joined)[_APRIL]
    assert (april["through_date"], april["covered_days"], april["window_days"]) == (date(2026, 4, 30), 30, 30)
    # 4 trips a day for 30 days planned, the 4 reported on the 1st observed.
    assert (april["observed_trips"], april["planned_trips"], april["rt_completeness_pct"]) == (4, 120, 3.3)
    # 2 stops of each: 8 stop events reported of 240 planned.
    assert (april["observed_stop_events"], april["planned_stop_events"], april["stop_plan_days"]) == (8, 240, 30)
    assert april["stop_completeness_pct"] == 3.3
    assert (april["raw_samples"], april["clamp_count"], float(april["clamp_pct"])) == (32, 1, 3.13)
    assert (april["message_age_polls"], april["message_age_p50_sec"], april["message_age_p95_sec"]) == (4, 10.0, 35.5)
    assert april["stop_time_updates"] == 32
    assert all(float(april[f"{field}_pct"]) == 100.0 for field in ("stop_id", "arr_delay"))
    assert april["vehicle_positions_missing"] == "not_recorded"

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
    compute_network_summary.cache_clear()
    board = {
        row["agency_id"]: row
        for row in await compute_network_summary(aconn, ch_async_client, _APRIL, date(2026, 4, 30))
    }
    assert float(april["rt_completeness_pct"]) == board[joined]["coverage_pct"]
    assert float(april["clamp_pct"]) == board[joined]["clamp_pct"]

    other = _stored(pg_conn, unmatched)[_APRIL]
    assert (other["rt_completeness_pct"], other["rt_completeness_missing"]) == (None, "coverage_unknown")
    assert (other["stop_completeness_pct"], other["stop_completeness_missing"]) == (None, "coverage_unknown")
    assert other["field_coverage_missing"] == "feed_cannot_report"
    # Archive ingest stamps captured_at from the header itself.
    assert (other["message_age_p50_sec"], other["message_age_missing"]) == (None, "archive_stamped")


def test_the_month_in_progress_counts_its_closed_days_and_says_why_a_figure_is_missing(
    pg_conn, ch_client, closed_on_may_first
):
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2)
    insert_updates(ch_client, agency, _live_polls())
    analyze(agency, pg_conn, ch_client)

    may = _stored(pg_conn, agency)[_MAY]
    assert (may["through_date"], may["window_days"], may["covered_days"]) == (_MAY, 1, 0)
    assert (may["rt_completeness_pct"], may["rt_completeness_missing"]) == (None, "no_plan")
    assert (may["stop_completeness_pct"], may["stop_completeness_missing"]) == (None, "no_plan")
    assert (may["message_age_missing"], may["clamp_missing"], may["field_coverage_missing"]) == (
        "no_data",
        "no_data",
        "no_data",
    )


def test_a_run_with_nothing_new_rebuilds_only_the_month_whose_window_grew(pg_conn, ch_client, monkeypatch):
    freeze_now(monkeypatch, feed_quality, jst_midday(date(2026, 4, 11)))
    agency = _agency(pg_conn, "joined", "static_join")
    _timetable(pg_conn, agency, stops=2)
    insert_updates(ch_client, agency, _live_polls())
    analyze(agency, pg_conn, ch_client)
    april = _stored(pg_conn, agency)[_APRIL]
    assert (april["through_date"], april["planned_trips"]) == (date(2026, 4, 10), 40)

    freeze_now(monkeypatch, feed_quality, jst_midday(date(2026, 4, 21)))
    analyze(agency, pg_conn, ch_client)

    # No feed row arrived, yet the ten closed days count against the feed.
    april = _stored(pg_conn, agency)[_APRIL]
    assert (april["through_date"], april["observed_trips"], april["planned_trips"]) == (date(2026, 4, 20), 4, 80)


def test_a_day_saved_without_its_stop_events_is_left_out_of_the_stop_ratio(pg_conn, ch_client, closed_on_may_first):
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
