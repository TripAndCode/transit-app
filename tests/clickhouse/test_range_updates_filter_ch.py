"""Unit + integration coverage for api.range's ClickHouse-dialect filter
builders (updates_filter_ch and its clause helpers), which the
pipeline/reports queries over the live ClickHouse `updates` table filter
through.

The unit tests run without any DB and assert only the shape of the generated
SQL fragment and its bound parameters: that the JST-aware and normalizing
expressions are present, not that ClickHouse evaluates them to the intended
rows. That behavioral proof (JST day bucketing, ISO weekday numbering, band
placement of 5-char and >= 24h scheduled times) comes from the
RUN_CH_INTEGRATION=1 tests below, which need a real ClickHouse
(`make ch-test`) and skip otherwise — the same split
tests/clickhouse/test_db_dedup_ch.py uses for build_dedup_ch_sql.
"""

import os
from datetime import date, datetime, timezone
from typing import Any

import pytest

from api.range import (
    RangeCtx,
    date_range_clause_ch,
    dow_clause_ch,
    scheduled_sec_ch,
    time_band_ch,
    time_band_clause_ch,
    updates_filter_ch,
)
from pipeline.service_day import service_date_ch
from tests.fixtures.service_dates import selecting


def _ctx(**over: Any) -> RangeCtx:
    base: dict[str, Any] = dict(
        from_date=date(2026, 5, 13),
        to_date=date(2026, 6, 11),
        dow="all",
        time_band="all",
        service="all",
        routes=(),
    )
    base.update(over)
    return RangeCtx(**base)


def test_date_range_clause_ch_uses_the_service_date_not_bare_todate():
    frag, params = date_range_clause_ch(_ctx())
    assert service_date_ch() in frag
    assert "toDate(captured_at)" not in frag.replace(service_date_ch(), "")
    assert params["ch_from_date"] == date(2026, 5, 13)
    assert params["ch_to_date"] == date(2026, 6, 11)


def test_dow_clause_ch_all_is_noop():
    frag, params = dow_clause_ch(_ctx(dow="all"))
    assert frag == "1"
    assert params == {}


def test_dow_clause_ch_weekday_matches_isodow_1_to_5():
    frag, _ = dow_clause_ch(_ctx(dow="weekday"))
    assert "BETWEEN 1 AND 5" in frag
    assert "toDayOfWeek" in frag
    assert "Asia/Tokyo" in frag


def test_dow_clause_ch_weekend_is_6_and_7():
    frag, _ = dow_clause_ch(_ctx(dow="weekend"))
    assert "IN (6, 7)" in frag


def test_time_band_clause_ch_all_is_noop():
    frag, params = time_band_clause_ch(_ctx(time_band="all"))
    assert frag == "1"
    assert params == {}


def test_time_band_clause_ch_tests_one_range_on_the_clock_it_computes_once():
    """The live filter runs over every row of the scan, so it computes the
    departure's clock once and tests only the named band's range on it; the
    behavioural proof that it selects what analyze stores is
    tests/pipeline/test_extended_hours.py."""
    frag, params = time_band_clause_ch(_ctx(time_band="morning"))
    assert params == {"ch_tb_start": 5 * 3600, "ch_tb_end": 9 * 3600}
    assert "{ch_tb_start:UInt32}" in frag and "{ch_tb_end:UInt32}" in frag
    assert frag.count(scheduled_sec_ch("u.")) == 1
    assert "multiIf" not in frag


@pytest.mark.parametrize(
    ("sql", "prefix"), [(time_band_clause_ch(_ctx(time_band="night"))[0], "u."), (time_band_ch("d."), "d.")]
)
def test_the_band_reads_scheduled_sec_before_it_parses_the_string(sql, prefix):
    """`if` evaluates only the branch a row takes, where `ifNull` evaluates
    both, so a row carrying `scheduled_sec` never pays for parsing
    `scheduled_time`; and the seconds are computed once, however many bands
    test them."""
    assert f"ifNull({prefix}scheduled_sec" not in sql
    assert f"if({prefix}scheduled_sec IS NOT NULL, {prefix}scheduled_sec, " in sql
    assert sql.count(scheduled_sec_ch(prefix)) == 1


def test_time_band_clause_ch_unknown_band_is_noop_not_keyerror():
    """RangeCtx is typed, but it is also built from stored/client JSON; an
    unrecognised band must degrade to "no time filter", never raise."""
    frag, params = time_band_clause_ch(_ctx(time_band="brunch"))
    assert frag == "1"
    assert params == {}


def test_updates_filter_ch_default_date_only():
    where = updates_filter_ch(_ctx())
    frag, params = where.raw, where.params
    assert where.filed == ""
    assert "toDate(captured_at" in frag
    assert "toDayOfWeek" not in frag
    assert "scheduled_time" not in frag
    assert "service_type" not in frag
    assert "route_code" not in frag
    assert set(params) == {"ch_from_date", "ch_to_date"}


def _resolved(service, *dates: date):
    return selecting(service, date(2026, 5, 13), date(2026, 6, 11), *dates)


def test_updates_filter_ch_adds_service_dates_and_routes():
    resolved = _resolved("平日", date(2026, 5, 13), date(2026, 5, 14))
    where = updates_filter_ch(_ctx(service="平日", service_dates=resolved, routes=("R1", "R2")))
    frag, params = where.raw, where.params
    assert f"{service_date_ch()} IN {{ch_service_dates:Array(Date)}}" in frag
    assert "service_type" not in frag
    # A row with no route is kept before filing, which can give it one; the
    # route itself is tested on the filed row.
    assert "(route_code IN {ch_routes:Array(String)} OR route_code IS NULL)" in frag
    assert where.filed == "route_code IN {ch_routes:Array(String)}"
    assert params["ch_service_dates"] == [date(2026, 5, 13), date(2026, 5, 14)]
    assert params["ch_routes"] == ["R1", "R2"]


def test_updates_filter_ch_all_clauses_combined():
    resolved = _resolved("土日祝", date(2026, 5, 16), date(2026, 5, 17))
    ctx = _ctx(dow="weekend", time_band="evening", service="土日祝", service_dates=resolved, routes=("R9",))
    where = updates_filter_ch(ctx)
    params = where.params
    assert " AND " in where.raw
    # The band reads the scheduled time, which filing can rewrite.
    assert "scheduled_sec" in where.filed and "scheduled_sec" not in where.raw
    for name in ("ch_from_date", "ch_to_date", "ch_tb_start", "ch_tb_end", "ch_service_dates", "ch_routes"):
        assert name in params


def _ch_test_client():
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_updates_filter_ch_buckets_date_range_by_jst_day_not_utc_day():
    """A captured_at of 2026-01-01 20:00 UTC is 2026-01-02 05:00 JST. A
    date-range filter of [2026-01-02, 2026-01-02] must include this row
    (JST bucketing); a bare-UTC toDate() would put it in 2026-01-01 and
    wrongly exclude it — the same bug class test_db_dedup_ch.py guards for
    the dedup builder, here for the ctx date-range filter instead."""
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    insert_updates(
        client,
        1,
        [
            (
                "a/1.pb",
                datetime(2026, 1, 1, 20, 0, 0, tzinfo=timezone.utc),
                "T1",
                "weekday",
                "10:00:00",
                "R1",
                1,
                30,
            )
        ],
    )
    ctx = _ctx(from_date=date(2026, 1, 2), to_date=date(2026, 1, 2))
    frag, _, params = updates_filter_ch(ctx)
    result = client.query(
        f"SELECT count() FROM updates WHERE agency_id = {{agency_id:UInt16}} AND {frag}",
        parameters={"agency_id": 1, **params},
    )
    assert result.result_rows[0][0] == 1
    # Sanity check the inverse: the UTC day [2026-01-01, 2026-01-01] must NOT
    # match — otherwise this "test" would pass even with a bare-UTC bug.
    ctx_utc_day = _ctx(from_date=date(2026, 1, 1), to_date=date(2026, 1, 1))
    frag2, _, params2 = updates_filter_ch(ctx_utc_day)
    result2 = client.query(
        f"SELECT count() FROM updates WHERE agency_id = {{agency_id:UInt16}} AND {frag2}",
        parameters={"agency_id": 1, **params2},
    )
    assert result2.result_rows[0][0] == 0
    client.close()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_dow_clause_ch_matches_python_isoweekday():
    """toDayOfWeek(toDate(...)) must return the same 1=Mon..7=Sun numbering
    as Python's date.isoweekday() (== Postgres EXTRACT(ISODOW)) — cross-check
    against a real ClickHouse server rather than assuming default-mode
    semantics from memory."""
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    # 2026-08-03 is a Monday (isoweekday()==1); JST captured_at chosen well
    # inside the day so no UTC/JST boundary ambiguity is in play here.
    captured_at = datetime(2026, 8, 3, 3, 0, 0, tzinfo=timezone.utc)  # 12:00 JST, same JST day
    assert date(2026, 8, 3).isoweekday() == 1
    insert_updates(
        client,
        1,
        [("a/1.pb", captured_at, "T1", "weekday", "10:00:00", "R1", 1, 30)],
    )
    ctx = _ctx(dow="weekday", from_date=date(2026, 8, 3), to_date=date(2026, 8, 3))
    frag, _, params = updates_filter_ch(ctx)
    result = client.query(
        f"SELECT count() FROM updates WHERE agency_id = {{agency_id:UInt16}} AND {frag}",
        parameters={"agency_id": 1, **params},
    )
    assert result.result_rows[0][0] == 1

    ctx_weekend = _ctx(dow="weekend", from_date=date(2026, 8, 3), to_date=date(2026, 8, 3))
    frag_we, _, params_we = updates_filter_ch(ctx_weekend)
    result_we = client.query(
        f"SELECT count() FROM updates WHERE agency_id = {{agency_id:UInt16}} AND {frag_we}",
        parameters={"agency_id": 1, **params_we},
    )
    assert result_we.result_rows[0][0] == 0
    client.close()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_time_band_clause_ch_boundary_matches_5char_scheduled_time():
    """Agency 1 (青森市バス, aomori_regex ingest strategy) writes 5-char
    "HH:MM" `scheduled_time` values (see pipeline/strategies/aomori_regex.py
    — no seconds), unlike every static_join agency's 8-char "HH:MM:SS".
    A Postgres TIME column would normalize both to the same internal value;
    ClickHouse's `String` column does not, so a raw lexicographic compare
    puts every band boundary (05:00, 09:00, ...) in the PREVIOUS band
    instead of its own.

    A trip scheduled at exactly 09:00 (the morning/forenoon boundary) must
    land in "forenoon" (its own band, [09:00, 12:00)), never "morning"
    ([05:00, 09:00)) — a raw compare would match the previous band because
    it treats the 5-char form as lexicographically less than its own 8-char
    equivalent."""
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    insert_updates(
        client,
        1,
        [
            (
                "a/1.pb",
                datetime(2026, 8, 3, 3, 0, 0, tzinfo=timezone.utc),  # 12:00 JST
                "T1",
                "weekday",
                "09:00",  # 5-char, no seconds — aomori_regex style
                "R1",
                1,
                30,
            )
        ],
    )

    def _count(time_band):
        frag, params = time_band_clause_ch(_ctx(time_band=time_band))
        result = client.query(
            f"SELECT count() FROM updates AS u WHERE agency_id = {{agency_id:UInt16}} AND {frag}",
            parameters={"agency_id": 1, **params},
        )
        return result.result_rows[0][0]

    assert _count("forenoon") == 1, "09:00 must land in its own band (forenoon starts at 09:00)"
    assert _count("morning") == 0, "09:00 must NOT fall back into the previous band (morning ends at 09:00)"
    client.close()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_time_band_clause_ch_places_extended_hour_in_its_same_day_band():
    """A "25:30:00" departure is 01:30 on the following calendar day, so it
    belongs to `late_night` ([00:00, 05:00)). Ingest stores it as ingest
    stores every extended hour: no `scheduled_time` string, only its
    `scheduled_sec`."""
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    captured = datetime(2026, 8, 3, 16, 30, 0, tzinfo=timezone.utc)  # 01:30 JST the next day
    # stop_id, arr_delay, both schedule relationships and feed_timestamp precede scheduled_sec.
    row = ("a/1.pb", captured, "T1", "weekday", None, "R1", 1, 30, None, None, None, None, None, 25 * 3600 + 30 * 60)
    insert_updates(client, 1, [row])

    def _count(time_band):
        frag, params = time_band_clause_ch(_ctx(time_band=time_band))
        result = client.query(
            f"SELECT count() FROM updates AS u WHERE agency_id = {{agency_id:UInt16}} AND {frag}",
            parameters={"agency_id": 1, **params},
        )
        return result.result_rows[0][0]

    assert _count("late_night") == 1, "25:30 wraps to 01:30, inside late_night [00:00, 05:00)"
    assert _count("night") == 0, "25:30 must not stay in the 20:00-24:00 band it lexicographically sorts after"
    client.close()
