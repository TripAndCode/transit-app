"""Pure-math tests for pipeline.prediction_accuracy (no DB).

Covers the "known final vs. early prediction produces the expected bucketed
error" fixture item 96's own acceptance criteria call for.
"""

from datetime import date, datetime, time, timezone

from pipeline.prediction_accuracy import (
    LEAD_HI,
    LEAD_LO,
    LEAD_N_BUCKETS,
    aggregate_by_lead_bucket,
    bucketize_lead_time,
    compute_stop_event_errors,
    prediction_error,
    rows_to_lead_bucket_stats,
    scheduled_departure_at,
)


def test_scheduled_departure_at_combines_date_and_time_in_jst():
    dep = scheduled_departure_at(date(2026, 4, 1), time(9, 0, 0))
    assert dep == datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)  # 09:00 JST == 00:00 UTC
    assert dep.utcoffset().total_seconds() == 9 * 3600


def test_scheduled_departure_at_accepts_raw_ch_string_form():
    """`scheduled_time` arrives from ClickHouse as a bare string, not a
    `datetime.time` -- both the aomori_regex ("HH:MM") and static_join
    ("HH:MM:SS") on-wire forms must parse to the same instant a `time`
    object would."""
    expected = scheduled_departure_at(date(2026, 4, 1), time(9, 0, 0))
    assert scheduled_departure_at(date(2026, 4, 1), "09:00") == expected
    assert scheduled_departure_at(date(2026, 4, 1), "09:00:00") == expected


def test_prediction_error_basic_math():
    scheduled_departure = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
    early_captured_at = datetime(2026, 3, 31, 23, 50, 0, tzinfo=timezone.utc)  # 10 min before scheduled departure
    lead_time_sec, error_sec = prediction_error(scheduled_departure, early_captured_at, 600, 120)
    # predicted_departure = scheduled_departure + 600s = 00:10 UTC (10 min after scheduled)
    # lead_time = predicted_departure - early_captured_at = 20 minutes
    assert lead_time_sec == 20 * 60
    assert error_sec == 480  # 600 - 120: the early observation overestimated the eventual delay


def test_bucketize_lead_time_bounds():
    assert bucketize_lead_time(LEAD_LO) == 1  # first inner bucket starts at LEAD_LO
    assert bucketize_lead_time(LEAD_LO - 1) == 0  # underflow (negative lead time)
    assert bucketize_lead_time(LEAD_HI) == bucketize_lead_time(LEAD_HI + 10_000)  # both overflow
    assert bucketize_lead_time(LEAD_HI) == LEAD_N_BUCKETS - 1


def test_compute_stop_event_errors_known_fixture():
    """Three observations for one stop event: two early, one final. Verifies
    both the lead-time bucketing and the signed error direction against
    hand-computed values."""
    scheduled_departure = datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)
    final_captured_at = datetime(2026, 4, 1, 8, 58, 0, tzinfo=timezone.utc)  # dep_delay=120, the final observation
    # A: predicted dep_delay=600 -> predicted departure 09:10, observed at 08:39 -> 31 min lead.
    early_a_captured_at = datetime(2026, 4, 1, 8, 39, 0, tzinfo=timezone.utc)
    # B: predicted dep_delay=60 -> predicted departure 09:01, observed at 08:55 -> 6 min lead.
    early_b_captured_at = datetime(2026, 4, 1, 8, 55, 0, tzinfo=timezone.utc)

    observations = [
        (early_a_captured_at, 600),
        (final_captured_at, 120),  # max captured_at -> excluded as "the final observation"
        (early_b_captured_at, 60),
    ]
    errors = compute_stop_event_errors(scheduled_departure, observations)

    assert len(errors) == 2  # excludes the final (max captured_at) observation
    by_error = {e["error_sec"]: e for e in errors}
    assert by_error[480]["lead_time_sec"] == 31 * 60  # A: 600 - 120 overestimate, 31 min lead
    assert by_error[480]["lead_bucket"] == LEAD_N_BUCKETS - 1  # 31 min > LEAD_HI -> overflow bucket
    assert by_error[-60]["lead_time_sec"] == 6 * 60  # B: 60 - 120 underestimate, 6 min lead
    assert by_error[-60]["lead_bucket"] == 4  # inner bucket for a 360s lead time (LEAD_WIDTH=120)


def test_compute_stop_event_errors_single_observation_yields_nothing():
    scheduled_departure = datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)
    observations = [(datetime(2026, 4, 1, 8, 58, 0, tzinfo=timezone.utc), 120)]
    assert compute_stop_event_errors(scheduled_departure, observations) == []


def test_aggregate_by_lead_bucket_merges_across_events():
    scheduled_departure = datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)
    # Two stop events sharing the exact same early (captured_at, dep_delay) --
    # lead_time_sec depends only on scheduled_departure/early_captured_at/
    # early_dep_delay, so this guarantees both land in the SAME lead-time
    # bucket regardless of how their (differing) finals turn out -- with
    # opposite-signed errors against those differing finals.
    # event_1's error: 300 - 60 = 240 (overestimate).
    event_1 = compute_stop_event_errors(
        scheduled_departure,
        [
            (datetime(2026, 4, 1, 8, 55, 0, tzinfo=timezone.utc), 300),  # early
            (datetime(2026, 4, 1, 8, 59, 0, tzinfo=timezone.utc), 60),  # final
        ],
    )
    # event_2's error: 300 - 420 = -120 (underestimate).
    event_2 = compute_stop_event_errors(
        scheduled_departure,
        [
            (datetime(2026, 4, 1, 8, 55, 0, tzinfo=timezone.utc), 300),  # early: same lead time as event_1
            (datetime(2026, 4, 1, 8, 59, 0, tzinfo=timezone.utc), 420),  # final
        ],
    )
    merged = aggregate_by_lead_bucket(event_1 + event_2)

    assert len(merged) == 1
    row = merged[0]
    assert row["samples"] == 2
    assert row["avg_error_sec"] == (240 + -120) / 2
    assert row["avg_abs_error_sec"] == (240 + 120) / 2


def test_aggregate_by_lead_bucket_empty_input():
    assert aggregate_by_lead_bucket([]) == []


def test_rows_to_lead_bucket_stats_consumes_ch_row_shape():
    """End-to-end against the exact row shape
    build_prediction_accuracy_ch_sql produces: (route_code, service_type,
    scheduled_time, trip_id, date, stop_sequence, final_dep_delay,
    observations)."""
    row = (
        "R1",
        "weekday",
        time(9, 0, 0),
        "T1",
        date(2026, 4, 1),
        1,
        120,  # final_dep_delay (argMax) -- read for documentation only
        [
            (datetime(2026, 4, 1, 8, 55, 0, tzinfo=timezone.utc), 300),
            (datetime(2026, 4, 1, 8, 59, 0, tzinfo=timezone.utc), 120),
        ],
    )
    stats = rows_to_lead_bucket_stats([row])
    assert len(stats) == 1
    assert stats[0]["samples"] == 1
    assert stats[0]["avg_error_sec"] == 180  # 300 - 120
