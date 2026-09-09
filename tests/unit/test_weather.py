"""Pure-logic tests for the observed-weather ingest and the rain-vs-dry
comparison's shaping -- no DB, no network (see `pipeline.weather` and
`pipeline.reports.weather` for the invariants exercised here)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline.reports.weather import (
    MIN_DAYS_PER_GROUP,
    WET_DAY_PRECIP_MM,
    observation_disclaimer,
    summarize_rain_delay,
)
from pipeline.weather import (
    _MAX_BLOCK_BYTES,
    _MAX_DAY_BYTES,
    PUBLICATION_WINDOW_DAYS,
    DailyObservation,
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

    def _fake_block(station_id, day, hour, max_bytes, timeout=None):
        caps.append(max_bytes)
        # Each block consumes its whole allowance.
        return ({}, max_bytes)

    monkeypatch.setattr(weather, "_fetch_block", _fake_block)
    assert fetch_daily_observation("99999", _DAY) is None
    assert sum(caps) <= _MAX_DAY_BYTES
    assert caps and caps[0] == min(_MAX_BLOCK_BYTES, _MAX_DAY_BYTES)
    # Fewer than the nine blocks a day would otherwise read: the budget ran out.
    assert len(caps) < 9


def test_fetch_daily_observation_abandons_a_day_that_exhausts_the_deadline(monkeypatch):
    """A station-day reads nine blocks, each otherwise entitled to its own full
    socket timeout, so a caller's deadline has to reach inside the day: a slow
    source must stop the day where it stands rather than overrun by another
    eight timeouts. What it stops is an unavailable day, not a partial one --
    no observation comes back, so nothing is stored and a later pass retries."""
    import pipeline.weather as weather

    clock = _FakeClock(cost_sec=20.0)
    monkeypatch.setattr(weather, "time", clock)
    timeouts: list[float] = []

    def _slow_block(station_id, day, hour, max_bytes, timeout=weather._FETCH_TIMEOUT_SEC):
        timeouts.append(timeout)
        clock.spend()
        # A whole, usable day's readings: what stops this day is the deadline,
        # not anything missing from the source.
        return (_readings(_DAY, precip_per_slot=0.5), 1024)

    monkeypatch.setattr(weather, "_fetch_block", _slow_block)

    # 50s of budget against blocks that each burn a full 20s timeout.
    assert fetch_daily_observation("99999", _DAY, deadline=clock.monotonic() + 50.0) is None
    # Three blocks, not the nine a whole day needs: the third leaves 10s, and
    # the fourth's check finds the deadline already spent.
    assert timeouts == [20.0, 20.0, 10.0]


def test_fetch_daily_observation_without_a_deadline_reads_the_whole_day(monkeypatch):
    """The deadline is optional -- a standalone CLI run passes none, and then
    every block is fetched with the full per-socket ceiling."""
    import pipeline.weather as weather

    timeouts: list[float] = []

    def _block(station_id, day, hour, max_bytes, timeout=weather._FETCH_TIMEOUT_SEC):
        timeouts.append(timeout)
        return (_readings(_DAY, precip_per_slot=0.5), 1024)

    monkeypatch.setattr(weather, "_fetch_block", _block)

    obs = fetch_daily_observation("99999", _DAY)
    assert obs is not None
    assert timeouts == [weather._FETCH_TIMEOUT_SEC] * 9


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


def test_needs_fetch_true_when_never_stored_inside_the_window():
    assert needs_fetch(_DAY, None, today=_DAY + timedelta(days=1), now=datetime.now(timezone.utc)) is True


def test_needs_fetch_true_for_a_never_stored_day_on_the_last_day_of_the_window():
    """The window is inclusive at its edge: the oldest day the source still
    publishes is exactly `PUBLICATION_WINDOW_DAYS` old, and a pass walking back
    that far must still try it."""
    today = _DAY + timedelta(days=PUBLICATION_WINDOW_DAYS)
    now = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    assert needs_fetch(_DAY, None, today=today, now=now) is True


def test_needs_fetch_false_for_a_never_stored_day_past_the_publication_window():
    """A day that was never stored while it was fetchable can never be filled:
    the source has stopped publishing it, so asking again would 404 on this pass
    and on every pass after it. Age decides on its own, before storage does."""
    today = _DAY + timedelta(days=PUBLICATION_WINDOW_DAYS + 1)
    now = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    assert needs_fetch(_DAY, None, today=today, now=now) is False


def test_needs_fetch_false_for_a_recently_retrieved_copy():
    """An ingest pass that runs far more often than the source revises must not
    re-fetch the same day every time."""
    now = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)
    assert needs_fetch(_DAY, now - timedelta(hours=1), today=date(2026, 4, 2), now=now) is False


def test_needs_fetch_true_for_a_stale_copy_inside_the_publication_window():
    now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    assert needs_fetch(_DAY, now - timedelta(days=2), today=date(2026, 4, 3), now=now) is True


def test_needs_fetch_false_for_a_stored_day_past_the_publication_window():
    """Past the window the copy on hand is final -- the source no longer
    publishes that day, so re-fetching could only ever fail."""
    today = _DAY + timedelta(days=PUBLICATION_WINDOW_DAYS + 1)
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


class _FakeCursor:
    """Just enough of psycopg2's cursor for `ingest_weather`'s three statements."""

    def __init__(self, conn):
        self._conn = conn
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        # A statement opens a transaction, and a statement that raises leaves it
        # open and aborted -- only a commit or a rollback ends it.
        self._conn.in_txn = True
        if "agency_weather_stations" in sql:
            self._rows = [(s,) for s in self._conn.stations]
        elif "SELECT obs_date" in sql:
            self._conn.stored_ranges.append(tuple(params))
            self._rows = []  # nothing stored yet, so every day needs a fetch
        else:
            if (params[0], params[1]) in self._conn.poison:
                raise RuntimeError("simulated upsert failure")
            self._conn.pending.append(params[:2])

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Tracks what each commit actually persisted, so a test can tell a row that
    reached the database from one that was only ever upserted into a
    transaction that later rolled back."""

    def __init__(self, stations, poison=()):
        self.stations = stations
        self.poison = set(poison)
        self.pending: list[tuple] = []
        self.persisted: list[tuple] = []
        self.stored_ranges: list[tuple] = []
        self.in_txn = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.persisted.extend(self.pending)
        self.pending.clear()
        self.in_txn = False

    def rollback(self):
        self.pending.clear()
        self.in_txn = False


class _FakeClock:
    """Stands in for `pipeline.weather`'s `time`: a monotonic clock that moves
    only when a simulated fetch spends from it, so a budget test asserts the
    budget's logic rather than how fast the machine running it happens to be."""

    def __init__(self, *, cost_sec: float):
        self._now = 1000.0
        self._cost = cost_sec

    def monotonic(self) -> float:
        return self._now

    def spend(self) -> None:
        self._now += self._cost


def _patch_fetch(monkeypatch, weather, conn, *, clock=None, fetched=None, deadlines=None):
    """Replace the outbound fetch with an always-successful local stand-in."""

    def _fetch(station_id, obs_date, *, deadline=None):
        # No transaction may be open while the pass is out on the network.
        assert conn.in_txn is False
        if deadlines is not None:
            deadlines.append(deadline)
        if clock is not None:
            clock.spend()
        if fetched is not None:
            fetched.append((station_id, obs_date))
        return DailyObservation(
            station_id=station_id,
            obs_date=obs_date,
            precip_mm=1.0,
            temp_avg_c=20.0,
            temp_max_c=25.0,
            temp_min_c=15.0,
        )

    monkeypatch.setattr(weather, "fetch_daily_observation", _fetch)


def test_ingest_weather_keeps_the_days_a_failing_station_already_committed(monkeypatch):
    """Every station-day is committed on its own, so a station that raises
    part-way through keeps the days it already persisted and loses only the one
    in flight. The returned count has to follow what committed -- an operator
    reading "wrote N" and finding a different number of rows would have no way
    to tell the difference."""
    import pipeline.weather as weather

    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    conn = _FakeConn(["11111", "22222"], poison={("22222", date(2026, 4, 2))})
    _patch_fetch(monkeypatch, weather, conn)

    # Station 22222's second day fails, after its first day was already committed.
    written, considered, failed = weather.ingest_weather(conn, days=3, today=date(2026, 4, 4))

    assert failed == ["22222"]
    # 11111's three days, then 22222's first two -- its loop aborts on the second.
    assert considered == 5
    assert written == 4
    assert written == len(conn.persisted)
    assert conn.persisted == [
        ("11111", date(2026, 4, 1)),
        ("11111", date(2026, 4, 2)),
        ("11111", date(2026, 4, 3)),
        ("22222", date(2026, 4, 1)),
    ]
    # The failure was rolled back, so the caller's session is left usable.
    assert conn.in_txn is False


def test_ingest_weather_stops_once_the_wall_clock_budget_is_spent(monkeypatch):
    """The budget bounds the whole pass, not one request: checked before each
    station-day, it can stop a station part-way, and what it cuts short is left
    for a later pass rather than reported as a failure."""
    import pipeline.weather as weather

    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    conn = _FakeConn(["11111", "22222"])
    clock = _FakeClock(cost_sec=50.0)
    monkeypatch.setattr(weather, "time", clock)
    fetched: list[tuple] = []
    deadlines: list[float | None] = []
    _patch_fetch(monkeypatch, weather, conn, clock=clock, fetched=fetched, deadlines=deadlines)
    started_at = clock.monotonic()

    written, considered, failed = weather.ingest_weather(conn, days=3, today=date(2026, 4, 4), max_seconds=90.0)

    # Two fetches spend 100s of the 90s budget, so the third day's check stops
    # the pass mid-station and the second station is never begun.
    assert failed == []
    # The same budget is handed down into each station-day, so a slow source
    # stops a day mid-way instead of overrunning it by nine socket timeouts.
    assert deadlines == [started_at + 90.0, started_at + 90.0]
    assert considered == 2
    assert written == 2
    assert fetched == [("11111", date(2026, 4, 1)), ("11111", date(2026, 4, 2))]
    # Everything reached before the cutoff is still committed and reported.
    assert conn.persisted == fetched
    assert conn.in_txn is False


def test_ingest_weather_budget_stops_before_the_next_station_is_read(monkeypatch):
    """The budget is also checked before each station, so a pass that spent it
    finishing one station doesn't even read the next station's stored days --
    the check has to come ahead of the work, not just ahead of the fetch."""
    import pipeline.weather as weather

    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    conn = _FakeConn(["11111", "22222"])
    clock = _FakeClock(cost_sec=40.0)
    monkeypatch.setattr(weather, "time", clock)
    _patch_fetch(monkeypatch, weather, conn, clock=clock)

    written, considered, failed = weather.ingest_weather(conn, days=3, today=date(2026, 4, 4), max_seconds=90.0)

    # 11111's three days spend 120s of the 90s budget; 22222 is never started.
    assert (written, considered, failed) == (3, 3, [])
    assert [station_id for station_id, _ in conn.persisted] == ["11111"] * 3
    assert [station_id for station_id, *_ in conn.stored_ranges] == ["11111"]


def test_ingest_weather_without_a_budget_covers_every_station_day(monkeypatch):
    """`max_seconds=None` is unbounded: the same pass that the budget cut short
    walks all of it when no budget is imposed."""
    import pipeline.weather as weather

    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    conn = _FakeConn(["11111", "22222"])
    clock = _FakeClock(cost_sec=50.0)
    monkeypatch.setattr(weather, "time", clock)
    fetched: list[tuple] = []
    _patch_fetch(monkeypatch, weather, conn, clock=clock, fetched=fetched)

    written, considered, failed = weather.ingest_weather(conn, days=3, today=date(2026, 4, 4))

    assert failed == []
    assert considered == 6
    assert written == 6
    assert len(conn.persisted) == 6
    assert sorted({station_id for station_id, _ in fetched}) == ["11111", "22222"]


def test_ingest_weather_clamps_days_to_the_publication_window(monkeypatch):
    """An operator-supplied backfill window wider than the source's publication
    window is harmless: the walk itself is clamped, so no request is issued for
    a day that could only 404, and the stored-day lookup is narrowed with it."""
    import pipeline.weather as weather

    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    conn = _FakeConn(["11111"])
    fetched: list[tuple] = []
    _patch_fetch(monkeypatch, weather, conn, fetched=fetched)

    written, considered, failed = weather.ingest_weather(conn, days=30, today=date(2026, 4, 10))

    assert failed == []
    assert considered == PUBLICATION_WINDOW_DAYS
    assert written == PUBLICATION_WINDOW_DAYS
    # Yesterday back through the oldest day the source still publishes, no further.
    oldest = date(2026, 4, 9) - timedelta(days=PUBLICATION_WINDOW_DAYS - 1)
    assert [obs_date for _, obs_date in fetched] == [oldest + timedelta(days=i) for i in range(PUBLICATION_WINDOW_DAYS)]
    assert conn.stored_ranges == [("11111", oldest, date(2026, 4, 9))]


@pytest.mark.parametrize("switch", [None, "false", "", "0", "no"])
def test_ingest_weather_off_switch_touches_neither_the_source_nor_the_db(monkeypatch, switch):
    """The switch is what decides whether this deployment makes scheduled
    outbound requests to a third party at all, so its off state needs a
    negative control: unset, or set to anything that is not an affirmative
    value, the pass must issue no request and open no cursor -- not merely
    write nothing. Guarding the connection as well as the fetch is what would
    catch the check being moved out to the CLI, which would leave the cron
    path in `api.routers.internal` fetching on every poke with the switch
    off."""
    import pipeline.weather as weather

    if switch is None:
        monkeypatch.delenv("WEATHER_INGEST_ENABLED", raising=False)
    else:
        monkeypatch.setenv("WEATHER_INGEST_ENABLED", switch)

    def _refuse(*args, **kwargs):
        raise AssertionError("the off switch must not reach the source or the database")

    monkeypatch.setattr(weather, "fetch_daily_observation", _refuse)
    monkeypatch.setattr(weather, "safe_urlopen", _refuse)

    class _RefusingConn:
        cursor = _refuse
        commit = _refuse
        rollback = _refuse

    assert weather.ingest_weather(_RefusingConn(), days=3, today=date(2026, 4, 4)) == (0, 0, [])


def test_fetch_block_percent_encodes_the_station_id(monkeypatch):
    """`station_id` is hand-populated and lands in the URL's path, so a value
    carrying a `/` or `?` must not be able to steer the fetch at a different
    document on the source host."""
    import pipeline.weather as weather

    seen: list[str] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(url, *, timeout, max_bytes):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(weather, "safe_urlopen", _fake_urlopen)
    weather._fetch_block("47765/../../forecast", _DAY, 0, 1024)

    assert len(seen) == 1
    url = seen[0]
    assert "47765%2F..%2F..%2Fforecast" in url
    # The path still resolves to the point-observation file the template names.
    assert url.startswith("https://www.jma.go.jp/bosai/amedas/data/point/")
    assert url.endswith("/20260401_00.json")
