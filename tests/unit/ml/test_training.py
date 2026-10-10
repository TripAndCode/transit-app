from collections import Counter
from datetime import date, timedelta

from tests.unit.ml.ml_group import require

pd = require("pandas")

from ml.features import route_ids, with_route_ids  # noqa: E402
from ml.training import training_frame, training_targets  # noqa: E402

START = date(2026, 6, 1)


def _runs(days, skip=()):
    frame = pd.DataFrame(
        [
            {
                "agency_id": 8,
                "route_code": "R1",
                "trip_id": "T1",
                "service_date": pd.Timestamp(START + timedelta(days=d)),
                "hour": 8,
                "service": "wk",
                "delay_min": 2.0,
                "stops": 10,
                "span_min": 30.0,
            }
            for d in range(days)
            if d not in skip
        ]
    ).astype({"agency_id": "int16", "route_code": "string", "trip_id": "string", "hour": "int16", "service": "string"})
    return with_route_ids(frame, route_ids(frame))


def test_every_day_after_the_first_week_is_a_target_once_with_a_lead_of_one_to_seven():
    cutoff = START + timedelta(days=60)
    targets = training_targets(START, cutoff, seed=7)
    days = [day for group in targets.values() for day in group]
    assert sorted(days) == [START + timedelta(days=d) for d in range(8, 61)]
    for origin, group in targets.items():
        assert origin - timedelta(days=7) >= START
        assert all(1 <= (day - origin).days <= 7 for day in group)


def test_the_lead_does_not_follow_the_weekday():
    targets = training_targets(START, START + timedelta(days=200), seed=7)
    weekdays_per_lead = Counter()
    for lead in range(1, 8):
        weekdays = {day.weekday() for origin, group in targets.items() for day in group if (day - origin).days == lead}
        weekdays_per_lead[lead] = len(weekdays)
    assert all(count >= 5 for count in weekdays_per_lead.values()), weekdays_per_lead


def test_no_target_falls_after_the_cutoff_and_none_appears_twice():
    cutoff = START + timedelta(days=60)
    frame = training_frame(_runs(80), cutoff, window_days=28, half_life_days=14)
    assert frame["service_date"].max() <= pd.Timestamp(cutoff)
    assert not frame.duplicated(["trip_id", "service_date"]).any()


def test_recent_targets_weigh_more_halving_every_half_life():
    cutoff = START + timedelta(days=60)
    frame = training_frame(_runs(80), cutoff, window_days=28, half_life_days=14).set_index("service_date")
    assert frame.loc[pd.Timestamp(cutoff), "weight"] == 1.0
    assert abs(frame.loc[pd.Timestamp(cutoff - timedelta(days=14)), "weight"] - 0.5) < 1e-6


def test_a_gap_day_has_no_training_rows():
    cutoff = START + timedelta(days=60)
    gap = START + timedelta(days=50)
    frame = training_frame(_runs(80, skip=(50,)), cutoff, window_days=28, half_life_days=14)
    assert pd.Timestamp(gap) not in set(frame["service_date"])
    assert pd.Timestamp(gap + timedelta(days=1)) in set(frame["service_date"])
