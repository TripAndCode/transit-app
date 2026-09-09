"""Pure-logic tests for the observed-weather ingest and the rain-vs-dry
comparison's shaping -- no DB, no network (see `pipeline.weather` and
`pipeline.reports.weather` for the invariants exercised here)."""

from datetime import date, datetime, timedelta, timezone

from pipeline.reports.weather import (
    MIN_DAYS_PER_GROUP,
    WET_DAY_PRECIP_MM,
    observation_disclaimer,
    summarize_rain_delay,
)
from pipeline.weather import (
    _MAX_BLOCK_BYTES,
    _MAX_DAY_BYTES,
    REVISION_RECHECK_DAYS,
    aggregate_daily,
    attribution,
    fetch_daily_observation,
    needs_fetch,
)

_DAY = date(2026, 4, 1)
_STATION = {"station_id": "99999", "station_name": "Test Station", "note": "central depot"}


def _readings(
    day: date,
    *,
    precip_per_slot: float = 0.0,
    temp: float | None = 20.0,
    slots: int = 144,
    flag: int = 0,
) -> dict[str, dict]:
    """Build ``slots`` consecutive 10-minute readings closing out *day*.

    Stamped like the source does -- at the END of the interval measured -- so a
    full day is 00:10 .. 24:00, the last one landing on the NEXT day's 00:00.
    """
    out: dict[str, dict] = {}
    base = datetime(day.year, day.month, day.day)
    for i in range(1, slots + 1):
        ts = (base + timedelta(minutes=10 * i)).strftime("%Y%m%d%H%M%S")
        fields: dict = {"precipitation10m": [precip_per_slot, flag]}
        if temp is not None:
            fields["temp"] = [temp, 0]
        out[ts] = fields
    return out


def test_aggregate_daily_sums_a_whole_day_of_rainfall():
    obs = aggregate_daily("99999", _DAY, _readings(_DAY, precip_per_slot=0.5))
    assert obs is not None
    # 144 slots x 0.5 mm, summed exactly at the source's own 1-dp resolution.
    assert obs.precip_mm == 72.0
    assert obs.obs_date == _DAY
    assert obs.station_id == "99999"


def test_aggregate_daily_reports_a_dry_day_as_zero_not_none():
    """A day with no rain is a measurement of 0 mm -- distinguishable from a
    day with no measurement at all, which yields no observation."""
    obs = aggregate_daily("99999", _DAY, _readings(_DAY, precip_per_slot=0.0))
    assert obs is not None
    assert obs.precip_mm == 0.0


def test_aggregate_daily_rejects_a_partial_day():
    """Rainfall is a whole-day sum: a partially published day would understate
    it and read as drier than it was, so it must yield nothing."""
    assert aggregate_daily("99999", _DAY, _readings(_DAY, precip_per_slot=0.5, slots=143)) is None


def test_aggregate_daily_rejects_a_day_with_a_non_normal_quality_flag():
    """A value the source doesn't vouch for as a normal observation cannot be
    silently counted as 0 mm."""
    readings = _readings(_DAY, precip_per_slot=0.5)
    last = max(readings)
    readings[last] = {"precipitation10m": [None, 5], "temp": [20.0, 0]}
    assert aggregate_daily("99999", _DAY, readings) is None


def test_aggregate_daily_rejects_non_finite_readings():
    """NaN/Infinity must never reach storage: Postgres evaluates both
    `'NaN'::float8 >= 0` and `'Infinity'::float8 >= 0` as true, so the CHECK on
    precip_mm cannot stop them, and one such reading makes the day's sum
    non-finite -- a permanently wrong total that mis-files the day as rainy and
    poisons the wet side's rainfall average. Rejecting them here fails the day
    like any other unusable slot."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        readings = _readings(_DAY, precip_per_slot=0.5)
        readings[max(readings)] = {"precipitation10m": [bad, 0], "temp": [20.0, 0]}
        assert aggregate_daily("99999", _DAY, readings) is None


def test_aggregate_daily_drops_a_non_finite_temperature_without_losing_the_day():
    """Temperature is optional, so a non-finite one is dropped like any other
    unusable temperature reading rather than failing the day -- but it must not
    leak into the mean/extrema either."""
    readings = _readings(_DAY, precip_per_slot=0.5)
    for fields in readings.values():
        fields["temp"] = [float("inf"), 0]
    obs = aggregate_daily("99999", _DAY, readings)
    assert obs is not None
    assert obs.precip_mm == 72.0
    assert obs.temp_avg_c is None
    assert obs.temp_max_c is None
    assert obs.temp_min_c is None


def test_fetch_daily_observation_bounds_the_merged_blocks(monkeypatch):
    """A station-day merges nine blocks into one dict, in a process that is
    long-lived on the cron path, so the per-block cap alone is not the bound
    that matters: the remaining day budget must shrink as blocks are read and
    the day be abandoned once it is spent, rather than accumulating nine
    per-block-sized bodies."""
    import pipeline.weather as weather

    caps: list[int] = []

    def _fake_block(station_id, day, hour, max_bytes):
        caps.append(max_bytes)
        # Each block consumes its whole allowance.
        return ({}, max_bytes)

    monkeypatch.setattr(weather, "_fetch_block", _fake_block)
    assert fetch_daily_observation("99999", _DAY) is None
    assert sum(caps) <= _MAX_DAY_BYTES
    assert caps and caps[0] == min(_MAX_BLOCK_BYTES, _MAX_DAY_BYTES)
    # Fewer than the nine blocks a day would otherwise read: the budget ran out.
    assert len(caps) < 9


def test_aggregate_daily_ignores_readings_outside_the_day_window():
    """The window is (00:00, 24:00]: the day's own 00:00 reading measures the
    PREVIOUS day's last interval, and the next day's 00:00 reading closes this
    one out. A day given only its own 00:00..23:50 stamps is one short."""
    readings = _readings(_DAY, precip_per_slot=0.5)
    closing = max(readings)  # the next day's 00:00:00
    del readings[closing]
    readings[_DAY.strftime("%Y%m%d") + "000000"] = {"precipitation10m": [99.0, 0]}
    assert aggregate_daily("99999", _DAY, readings) is None

    # With the closing reading restored, the out-of-window 00:00 one is
    # ignored rather than added in.
    readings[closing] = {"precipitation10m": [0.5, 0], "temp": [20.0, 0]}
    obs = aggregate_daily("99999", _DAY, readings)
    assert obs is not None
    assert obs.precip_mm == 72.0


def test_aggregate_daily_yields_rain_without_temperature():
    """Temperature is optional and independent -- a station reporting rain but
    no usable temperature still produces a usable row."""
    obs = aggregate_daily("99999", _DAY, _readings(_DAY, precip_per_slot=0.0, temp=None))
    assert obs is not None
    assert obs.precip_mm == 0.0
    assert obs.temp_avg_c is None
    assert obs.temp_max_c is None
    assert obs.temp_min_c is None


def test_aggregate_daily_temperature_mean_max_min():
    readings = _readings(_DAY, precip_per_slot=0.0, temp=10.0)
    keys = sorted(readings)
    readings[keys[0]]["temp"] = [0.0, 0]
    readings[keys[-1]]["temp"] = [20.0, 0]
    obs = aggregate_daily("99999", _DAY, readings)
    assert obs is not None
    assert obs.temp_max_c == 20.0
    assert obs.temp_min_c == 0.0
    # 142 readings at 10.0 plus one 0.0 and one 20.0 -> still 10.0 exactly.
    assert obs.temp_avg_c == 10.0


def test_needs_fetch_true_when_never_stored():
    assert needs_fetch(_DAY, None, today=_DAY + timedelta(days=1), now=datetime.now(timezone.utc)) is True


def test_needs_fetch_false_for_a_recently_retrieved_copy():
    """An ingest pass that runs far more often than the source revises must not
    re-fetch the same day every time."""
    now = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)
    assert needs_fetch(_DAY, now - timedelta(hours=1), today=date(2026, 4, 2), now=now) is False


def test_needs_fetch_true_for_a_stale_copy_inside_the_revision_window():
    now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    assert needs_fetch(_DAY, now - timedelta(days=2), today=date(2026, 4, 3), now=now) is True


def test_needs_fetch_false_once_past_the_revision_window():
    """Past the window the copy on hand is final -- the source no longer
    publishes that day, so re-fetching could only ever fail."""
    today = _DAY + timedelta(days=REVISION_RECHECK_DAYS + 1)
    now = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    assert needs_fetch(_DAY, now - timedelta(days=30), today=today, now=now) is False


def test_attribution_names_the_source_and_the_processing_in_both_locales():
    """The public-data terms require both the source AND the fact that these
    daily figures are this application's own aggregation of it."""
    ja = attribution("ja")
    en = attribution("en")
    assert ja != en
    assert "気象庁" in ja
    assert "集計" in ja
    assert "Meteorological Agency" in en
    assert "aggregated" in en
    # Unknown locale falls back to Japanese rather than raising.
    assert attribution("fr") == ja


def _bucket(is_wet: bool, days: int, samples: int, sum_delay_sec: int, avg_precip_mm: float):
    return {
        "is_wet": is_wet,
        "days": days,
        "samples": samples,
        "sum_delay_sec": sum_delay_sec,
        "avg_precip_mm": avg_precip_mm,
    }


def test_summarize_rain_delay_pools_each_side_and_reports_the_difference():
    """Each side's average is the raw-seconds sum over the sample count (not a
    mean of per-day means), and the delta is rainy minus non-rainy."""
    out = summarize_rain_delay(
        [
            _bucket(True, days=10, samples=1000, sum_delay_sec=120_000, avg_precip_mm=12.3),
            _bucket(False, days=20, samples=4000, sum_delay_sec=360_000, avg_precip_mm=0.1),
        ],
        _STATION,
    )
    assert out["available"] is True
    assert out["wet"]["avg_delay_sec"] == 120.0
    assert out["dry"]["avg_delay_sec"] == 90.0
    assert out["delta_sec"] == 30.0
    assert out["low_confidence"] is False
    assert out["wet_day_threshold_mm"] == WET_DAY_PRECIP_MM
    assert out["station"]["station_id"] == "99999"


def test_summarize_rain_delay_unavailable_without_a_configured_station():
    out = summarize_rain_delay([], None)
    assert out["available"] is False
    assert out["station"] is None
    assert out["delta_sec"] is None


def test_summarize_rain_delay_unavailable_when_no_day_could_be_matched():
    """A configured station with no observation matched to any in-range service
    day has nothing to show -- callers render nothing rather than an empty
    frame."""
    out = summarize_rain_delay([], _STATION)
    assert out["available"] is False
    assert out["wet"]["days"] == 0
    assert out["dry"]["days"] == 0


def test_summarize_rain_delay_available_with_no_delta_when_no_rainy_days():
    """A window containing no rainy days is a real answer, not missing data:
    the dry side is still reported, the difference is not invented."""
    out = summarize_rain_delay(
        [_bucket(False, days=30, samples=6000, sum_delay_sec=180_000, avg_precip_mm=0.0)],
        _STATION,
    )
    assert out["available"] is True
    assert out["dry"]["avg_delay_sec"] == 30.0
    assert out["wet"]["days"] == 0
    assert out["wet"]["avg_delay_sec"] is None
    assert out["delta_sec"] is None
    assert out["low_confidence"] is True


def test_summarize_rain_delay_flags_low_confidence_on_a_thin_side():
    """Few days on one side makes the difference dominated by whatever else was
    unusual about those days -- the figures are still returned, flagged."""
    out = summarize_rain_delay(
        [
            _bucket(
                True,
                days=MIN_DAYS_PER_GROUP - 1,
                samples=1000,
                sum_delay_sec=120_000,
                avg_precip_mm=8.0,
            ),
            _bucket(False, days=20, samples=4000, sum_delay_sec=360_000, avg_precip_mm=0.1),
        ],
        _STATION,
    )
    assert out["delta_sec"] == 30.0
    assert out["low_confidence"] is True


def test_summarize_rain_delay_flags_low_confidence_on_a_thin_sample_count():
    out = summarize_rain_delay(
        [
            _bucket(True, days=10, samples=5, sum_delay_sec=600, avg_precip_mm=8.0),
            _bucket(False, days=20, samples=4000, sum_delay_sec=360_000, avg_precip_mm=0.1),
        ],
        _STATION,
    )
    assert out["low_confidence"] is True


def test_observation_disclaimer_denies_forecast_and_causation_in_both_locales():
    ja = observation_disclaimer("ja")
    en = observation_disclaimer("en")
    assert ja != en
    assert "予測" in ja and "原因" in ja
    assert "forecast" in en.lower() and "caused" in en.lower()
    assert observation_disclaimer("fr") == ja
