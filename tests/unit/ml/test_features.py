from datetime import date, timedelta

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")

from ml.features import FEATURES, build_frame, route_ids, weekend_days, with_route_ids  # noqa: E402

ORIGIN = date(2026, 9, 8)  # a Tuesday


def _day(offset: int) -> date:
    return ORIGIN + timedelta(days=offset)


def _runs(rows):
    """rows: (day offset, route, trip, hour, delay, service)."""
    frame = pd.DataFrame(
        [
            {
                "agency_id": 8,
                "route_code": route,
                "trip_id": trip,
                "service_date": pd.Timestamp(_day(offset)),
                "hour": hour,
                "service": service,
                "delay_min": delay,
                "stops": 10,
                "span_min": 30.0,
            }
            for offset, route, trip, hour, delay, service in rows
        ]
    ).astype(
        {
            "agency_id": "int16",
            "route_code": "string",
            "trip_id": "string",
            "hour": "int16",
            "service": "string",
            "delay_min": "float32",
            "stops": "int16",
            "span_min": "float32",
        }
    )
    return with_route_ids(frame, route_ids(frame))


def test_a_run_gets_b0_and_b1_from_its_slot_and_route_trends_from_recent_days():
    runs = _runs(
        [
            (-14, "R1", "T1", 8, 4.0, "wk"),
            (-7, "R1", "T1", 8, 2.0, "wk"),
            (-1, "R1", "T2", 8, 9.0, "wk"),
            (7, "R1", "T1", 8, 0.0, "wk"),
        ]
    )
    row = build_frame(runs, ORIGIN).iloc[0]
    assert row.h == 7
    assert row.slot_mean == 3.0 and row.slot_runs == 2 and row.slot_last == 2.0
    assert row.route_mean == 5.0 and row.route_prev == 9.0
    assert row.route_mean_7 == 5.5 and row.route_mean_3 == 9.0 and row.route_trend == 4.0
    assert row.trip_mean == 3.0 and row.trip_runs == 2
    assert row.agency_days == 3


def test_no_feature_reads_the_origin_day_or_later():
    history = [(-d, "R1", f"T{d % 3}", 8 + d % 2, float(d % 5), "wk") for d in range(1, 40)]
    future = [(d, "R1", f"T{d % 3}", 8 + d % 2, 1.0, "wk") for d in range(0, 9)]
    runs = _runs(history + future)
    later = runs["service_date"] >= pd.Timestamp(ORIGIN)
    shuffled = runs.assign(delay_min=runs["delay_min"].where(~later, 99.0))
    before, after = build_frame(runs, ORIGIN), build_frame(shuffled, ORIGIN)
    pd.testing.assert_frame_equal(before[FEATURES], after[FEATURES])


def test_a_route_with_no_history_still_gets_a_row_with_its_history_missing():
    runs = _runs([(-7, "R1", "T1", 8, 2.0, "wk"), (1, "NEW", "N1", 8, 3.0, "wk")])
    row = build_frame(runs, ORIGIN).set_index("route_code").loc["NEW"]
    assert np.isnan(row.slot_mean) and row.slot_runs == 0 and np.isnan(row.route_mean)


def test_an_origin_with_no_history_at_all_still_builds_its_rows():
    runs = _runs([(1, "R1", "T1", 8, 2.0, "wk")])
    frame = build_frame(runs, ORIGIN)
    assert len(frame) == 1 and frame.iloc[0].agency_days == 0


def test_a_weekend_service_on_a_weekday_reads_as_a_holiday():
    saturdays = [(-3 - 7 * k, "R1", "S", 9, 1.0, "holiday-svc") for k in range(4)]  # -3 is a Saturday
    weekdays = [(-1 - 7 * k, "R1", "W", 8, 1.0, "weekday-svc") for k in range(4)]
    targets = [(6, "R1", "S", 9, 0.0, "holiday-svc"), (6, "R1", "W", 8, 0.0, "weekday-svc")]  # +6 is a Monday
    frame = build_frame(_runs(saturdays + weekdays + targets), ORIGIN).set_index("trip_id")
    assert frame.loc["S"].service_weekend_share == 1.0
    assert frame.loc["W"].service_weekend_share == 0.0


def test_a_gap_day_is_missing_from_every_window_not_zero():
    runs = _runs([(-d, "R1", "T1", 8, 4.0, "wk") for d in range(2, 30)] + [(1, "R1", "T1", 8, 0.0, "wk")])
    row = build_frame(runs, ORIGIN).iloc[0]
    assert row.route_mean == 4.0 and np.isnan(row.route_prev)


def test_a_precomputed_weekend_table_on_the_same_runs_gives_the_same_frame():
    runs = _runs(
        [
            (-3, "R1", "S", 9, 1.0, "holiday-svc"),
            (-10, "R1", "S", 9, 1.0, "holiday-svc"),
            (-1, "R1", "W", 8, 1.0, "weekday-svc"),
            (6, "R1", "S", 9, 0.0, "holiday-svc"),
            (6, "R1", "W", 8, 0.0, "weekday-svc"),
        ]
    )
    direct = build_frame(runs, ORIGIN)
    precomputed = build_frame(runs, ORIGIN, precomputed_weekend_days=weekend_days(runs))
    pd.testing.assert_frame_equal(direct[FEATURES], precomputed[FEATURES])


def test_weekend_share_does_not_see_rows_on_or_after_the_origin():
    history = [(-3 - 7 * k, "R1", "S", 9, 1.0, "holiday-svc") for k in range(2)]  # weekend-only so far
    leaked_future = [(d, "R1", "LEAK", 9, 1.0, "holiday-svc") for d in range(0, 5)]  # same service, on/after origin
    target = [(6, "R1", "S", 9, 0.0, "holiday-svc")]
    runs = _runs(history + leaked_future + target)
    table = weekend_days(runs)  # built over rows that include origin day and later
    frame = build_frame(runs, ORIGIN, precomputed_weekend_days=table)
    assert frame.set_index("trip_id").loc["S"].service_weekend_share == 1.0
