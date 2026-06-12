from datetime import date

from api.range import _TIME_BAND_RANGES, RangeCtx, build_agg_stop_filter, time_band_case_sql


def _ctx(**over):
    # RangeCtx uses from_date/to_date (date objects), dow, time_band, service, routes.
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


def test_time_band_case_covers_all_bands_and_null():
    sql = time_band_case_sql("scheduled_time")
    assert "scheduled_time IS NULL" in sql and "'none'" in sql
    for band, (start, end) in _TIME_BAND_RANGES.items():
        assert f"'{band}'" in sql and f"'{start}'" in sql and f"'{end}'" in sql
    assert sql.strip().startswith("CASE") and sql.strip().endswith("END")


def test_build_agg_stop_filter_default_date_only():
    frag, _params, _n = build_agg_stop_filter(_ctx(), next_param=2)
    assert "date" in frag
    assert "service_type" not in frag and "time_band" not in frag


def test_build_agg_stop_filter_adds_service_and_band():
    frag, params, _n = build_agg_stop_filter(_ctx(service="平日", time_band="morning"), next_param=2)
    assert "service_type = $" in frag and "time_band = $" in frag
    assert "平日" in params and "morning" in params


def test_build_agg_stop_filter_weekend_dow():
    frag, _, _ = build_agg_stop_filter(_ctx(dow="weekend"), next_param=2)
    assert "ISODOW" in frag and "(6, 7)" in frag
