"""Unit + integration coverage for api.range's ClickHouse-dialect filter
builders (build_updates_filter_ch and friends) — the sibling of the asyncpg
build_updates_filter used by map.py's /route-shape and rankings.py's
_compare_ranking_live once they read the live `updates` table from
ClickHouse instead of Postgres.

Pure-logic tests (fragment shape) run without any DB. The JST-boundary test
needs a real ClickHouse instance (RUN_CH_INTEGRATION=1 / `make ch-test`) —
mirrors tests/unit/test_db_dedup_ch.py's proof for build_dedup_ch_sql: this
is the same "toDate(captured_at, 'Asia/Tokyo') not bare toDate(captured_at)"
bug class, guarded the same way, for a different SQL builder.
"""

import os
from datetime import date, datetime, timezone

import pytest

from api.range import (
    RangeCtx,
    build_updates_filter_ch,
    date_range_clause_ch,
    dow_clause_ch,
    time_band_clause_ch,
)


def _ctx(**over):
    base = dict(
        from_date=date(2026, 5, 13),
        to_date=date(2026, 6, 11),
        dow="all",
        time_band="all",
        service="all",
        routes=(),
    )
    base.update(over)
    return RangeCtx(**base)


def test_date_range_clause_ch_uses_jst_todate_not_bare_todate():
    frag, params = date_range_clause_ch(_ctx())
    assert "toDate(captured_at, 'Asia/Tokyo')" in frag
    assert "toDate(captured_at)" not in frag.replace("toDate(captured_at, 'Asia/Tokyo')", "")
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


def test_time_band_clause_ch_morning_band():
    frag, params = time_band_clause_ch(_ctx(time_band="morning"))
    # Compares a normalized 5-char "HH:MM" prefix, not the raw scheduled_time
    # string — agency 1 (aomori_regex ingest strategy) writes 5-char
    # "HH:MM" values with no seconds, while every other agency
    # (static_join) writes 8-char "HH:MM:SS". A raw lexicographic compare
    # of "09:00" against an 8-char bound like "09:00:00" is wrong (the
    # 5-char form sorts as "less than" its own 8-char equivalent), so both
    # sides must be normalized to 5 chars for the comparison to be exact
    # regardless of which ingest strategy wrote the row.
    assert "substring(scheduled_time, 1, 5) >=" in frag
    assert "substring(scheduled_time, 1, 5) <" in frag
    assert params["ch_tb_start"] == "05:00"
    assert params["ch_tb_end"] == "09:00"


def test_build_updates_filter_ch_default_date_only():
    frag, params = build_updates_filter_ch(_ctx())
    assert "toDate(captured_at" in frag
    assert "toDayOfWeek" not in frag
    assert "scheduled_time" not in frag
    assert "service_type" not in frag
    assert "route_code" not in frag
    assert set(params) == {"ch_from_date", "ch_to_date"}


def test_build_updates_filter_ch_adds_service_and_routes():
    frag, params = build_updates_filter_ch(_ctx(service="平日", routes=("R1", "R2")))
    assert "service_type = {ch_service:String}" in frag
    assert "route_code IN {ch_routes:Array(String)}" in frag
    assert params["ch_service"] == "平日"
    assert params["ch_routes"] == ["R1", "R2"]


def test_build_updates_filter_ch_all_clauses_combined():
    ctx = _ctx(dow="weekend", time_band="evening", service="土日祝", routes=("R9",))
    frag, params = build_updates_filter_ch(ctx)
    assert " AND " in frag
    for name in ("ch_from_date", "ch_to_date", "ch_tb_start", "ch_tb_end", "ch_service", "ch_routes"):
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
def test_build_updates_filter_ch_buckets_date_range_by_jst_day_not_utc_day():
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
    frag, params = build_updates_filter_ch(ctx)
    result = client.query(
        f"SELECT count() FROM updates WHERE agency_id = {{agency_id:UInt16}} AND {frag}",
        parameters={"agency_id": 1, **params},
    )
    assert result.result_rows[0][0] == 1
    # Sanity check the inverse: the UTC day [2026-01-01, 2026-01-01] must NOT
    # match — otherwise this "test" would pass even with a bare-UTC bug.
    ctx_utc_day = _ctx(from_date=date(2026, 1, 1), to_date=date(2026, 1, 1))
    frag2, params2 = build_updates_filter_ch(ctx_utc_day)
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
    frag, params = build_updates_filter_ch(ctx)
    result = client.query(
        f"SELECT count() FROM updates WHERE agency_id = {{agency_id:UInt16}} AND {frag}",
        parameters={"agency_id": 1, **params},
    )
    assert result.result_rows[0][0] == 1

    ctx_weekend = _ctx(dow="weekend", from_date=date(2026, 8, 3), to_date=date(2026, 8, 3))
    frag_we, params_we = build_updates_filter_ch(ctx_weekend)
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
    Under the old Postgres TIME column this didn't matter (Postgres
    normalizes both to the same internal value); ClickHouse's `String`
    column does not, so a raw lexicographic compare puts every band
    boundary (05:00, 09:00, ...) in the PREVIOUS band instead of its own.

    A trip scheduled at exactly 09:00 (the morning/forenoon boundary) must
    land in "forenoon" (its own band, [09:00, 12:00)), never "morning"
    ([05:00, 09:00)) — the bug this regresses would have matched the old
    (previous) band because a raw compare treats the 5-char form as
    lexicographically less than its own 8-char equivalent."""
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
            f"SELECT count() FROM updates WHERE agency_id = {{agency_id:UInt16}} AND {frag}",
            parameters={"agency_id": 1, **params},
        )
        return result.result_rows[0][0]

    assert _count("forenoon") == 1, "09:00 must land in its own band (forenoon starts at 09:00)"
    assert _count("morning") == 0, "09:00 must NOT fall back into the previous band (morning ends at 09:00)"
    client.close()
