"""End-to-end test: synthetic GTFS static+RT fixtures -> analyze() -> agg_* tables (item 21).

Existing pipeline tests either only exercise the *loading* step
(`test_static_loader.py`/`test_static_join.py`) or seed `updates` with
enough rows to produce SOME aggregate without checking its value is
numerically correct (`test_analyze.py`'s `_seed_updates`, whose delay values
were never chosen to make the resulting `avg_min`/percentiles easy to hand
verify). This module closes that gap: it loads a small synthetic GTFS
static schedule via `pipeline.static_loader.load_static`, inserts matching
synthetic `updates` rows directly into the throwaway ClickHouse, runs
`pipeline.analyze.analyze`, and asserts every touched `agg_*` row against
the hand-computed `expected` dict shipped with each pattern in
`tests.fixtures.synthetic_gtfs` — the single source of truth those fixtures
document as reusable by later frontend/Ask-tab checks (items 22/23) too.
"""

from tests.fixtures.synthetic_gtfs import (
    ALL_PATTERNS,
    SyntheticPattern,
    null_delays,
    outlier_spike,
    run_pattern,
    uniform_delays,
)


def _assert_numeric(actual, expected, pattern_name: str, label: str, places: int = 2) -> None:
    """Compare a possibly-NULL numeric aggregate column against its expected value.

    `expected=None` asserts an actual NULL rather than crashing on
    `float(None)` — kept for generality even though none of this module's
    current patterns produce one: `agg_route_stats`/`agg_route_hour`'s
    `PERCENTILE_DISC()`-based p50/p90 always resolve to an observed value for
    any non-empty group (see `synthetic_gtfs.uniform_delays`'s docstring).
    """
    if expected is None:
        assert actual is None, f"{pattern_name}.{label}: expected NULL, got {actual!r}"
    else:
        assert round(float(actual), places) == expected, f"{pattern_name}.{label}"


def _assert_agg_route_stats(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_route_stats"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT avg_min, p50_min, p90_min, late_5min_plus, on_time_pct, late5_pct, samples "
            "FROM agg_route_stats WHERE agency_id = %s AND route_code = %s AND service_type = %s",
            (agency_id, pattern.route_code, pattern.service_type),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_route_stats row"
    avg_min, p50_min, p90_min, late_5min_plus, on_time_pct, late5_pct, samples = row
    _assert_numeric(avg_min, exp["avg_min"], pattern.name, "avg_min")
    _assert_numeric(p50_min, exp["p50_min"], pattern.name, "p50_min")
    _assert_numeric(p90_min, exp["p90_min"], pattern.name, "p90_min")
    assert late_5min_plus == exp["late_5min_plus"], pattern.name
    _assert_numeric(on_time_pct, exp["on_time_pct"], pattern.name, "on_time_pct", places=1)
    _assert_numeric(late5_pct, exp["late5_pct"], pattern.name, "late5_pct", places=1)
    assert samples == exp["samples"], pattern.name


def _assert_agg_route_hour(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_route_hour"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT scheduled_time, avg_min, p50_min, p90_min, samples FROM agg_route_hour "
            "WHERE agency_id = %s AND route_code = %s AND service_type = %s",
            (agency_id, pattern.route_code, pattern.service_type),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_route_hour row"
    scheduled_time, avg_min, p50_min, p90_min, samples = row
    assert str(scheduled_time) == pattern.scheduled_time, pattern.name
    _assert_numeric(avg_min, exp["avg_min"], pattern.name, "avg_min")
    _assert_numeric(p50_min, exp["p50_min"], pattern.name, "p50_min")
    _assert_numeric(p90_min, exp["p90_min"], pattern.name, "p90_min")
    assert samples == exp["samples"], pattern.name


def _assert_agg_route_dow(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_route_dow"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT avg_min, samples FROM agg_route_dow "
            "WHERE agency_id = %s AND route_code = %s AND service_type = %s AND dow = %s",
            (agency_id, pattern.route_code, pattern.service_type, pattern.dow),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_route_dow row for dow={pattern.dow}"
    avg_min, samples = row
    assert round(float(avg_min), 2) == exp["avg_min"], pattern.name
    assert samples == exp["samples"], pattern.name


def _assert_agg_route_hour_dow(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_route_hour_dow"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT avg_min, samples FROM agg_route_hour_dow "
            "WHERE agency_id = %s AND route_code = %s AND service_type = %s AND dow = %s AND hour = 8",
            (agency_id, pattern.route_code, pattern.service_type, pattern.dow),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_route_hour_dow row"
    avg_min, samples = row
    assert round(float(avg_min), 2) == exp["avg_min"], pattern.name
    assert samples == exp["samples"], pattern.name


def _assert_agg_daily_trend(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_daily_trend"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT avg_min, samples FROM agg_daily_trend "
            "WHERE agency_id = %s AND route_code = %s AND service_type = %s AND date = %s",
            (agency_id, pattern.route_code, pattern.service_type, pattern.date),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_daily_trend row"
    avg_min, samples = row
    assert round(float(avg_min), 2) == exp["avg_min"], pattern.name
    assert samples == exp["samples"], pattern.name


def _assert_agg_route_daily(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_route_daily"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT avg_delay_sec, worst_delay_sec, trips_observed, samples FROM agg_route_daily "
            "WHERE agency_id = %s AND route_code = %s AND service_type = %s AND date = %s",
            (agency_id, pattern.route_code, pattern.service_type, pattern.date),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_route_daily row"
    avg_delay_sec, worst_delay_sec, trips_observed, samples = row
    assert avg_delay_sec == exp["avg_delay_sec"], pattern.name
    assert worst_delay_sec == exp["worst_delay_sec"], pattern.name
    assert trips_observed == exp["trips_observed"], pattern.name
    assert samples == exp["samples"], pattern.name


def _assert_agg_hour_daily(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_hour_daily"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT avg_min, samples FROM agg_hour_daily WHERE agency_id = %s AND date = %s AND hour = %s",
            (agency_id, pattern.date, exp["hour"]),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_hour_daily row for hour={exp['hour']}"
    avg_min, samples = row
    assert round(float(avg_min), 2) == exp["avg_min"], pattern.name
    assert samples == exp["samples"], pattern.name


def _assert_agg_stop_seq(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_stop_seq"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_name, avg_min, samples FROM agg_stop_seq "
            "WHERE agency_id = %s AND route_code = %s AND stop_sequence = 1",
            (agency_id, pattern.route_code),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_stop_seq row"
    stop_name, avg_min, samples = row
    assert stop_name == pattern.stop_name, pattern.name
    assert round(float(avg_min), 2) == exp["avg_min"], pattern.name
    assert samples == exp["samples"], pattern.name


def _assert_agg_stop_daily(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_stop_daily"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT time_band, delay_sum, samples FROM agg_stop_daily "
            "WHERE agency_id = %s AND stop_id = %s AND date = %s AND service_type = %s",
            (agency_id, pattern.stop_id, pattern.date, pattern.service_type),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_stop_daily row"
    time_band, delay_sum, samples = row
    assert time_band == pattern.time_band, pattern.name
    assert delay_sum == exp["delay_sum"], pattern.name
    assert samples == exp["samples"], pattern.name


def _assert_agg_stop_routes(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_stop_routes"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_codes FROM agg_stop_routes WHERE agency_id = %s AND stop_id = %s",
            (agency_id, pattern.stop_id),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_stop_routes row"
    assert row[0] == exp["route_codes"], pattern.name


def _assert_agg_feed_health(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    exp = pattern.expected["agg_feed_health"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT raw_samples, clamp_count FROM agg_feed_health WHERE agency_id = %s AND date = %s",
            (agency_id, pattern.date),
        )
        row = cur.fetchone()
    assert row is not None, f"{pattern.name}: no agg_feed_health row"
    raw_samples, clamp_count = row
    assert raw_samples == exp["raw_samples"], pattern.name
    assert clamp_count == exp["clamp_count"], pattern.name


# Every builder above, run for every pattern below — kept as one ordered
# tuple so a pattern-specific test failure names exactly which agg_* table
# diverged from the hand-computed `expected` dict.
_ASSERTIONS = (
    _assert_agg_route_stats,
    _assert_agg_route_hour,
    _assert_agg_route_dow,
    _assert_agg_route_hour_dow,
    _assert_agg_daily_trend,
    _assert_agg_route_daily,
    _assert_agg_hour_daily,
    _assert_agg_stop_seq,
    _assert_agg_stop_daily,
    _assert_agg_stop_routes,
    _assert_agg_feed_health,
)


def _assert_all(pattern: SyntheticPattern, pg_conn, agency_id) -> None:
    for assertion in _ASSERTIONS:
        assertion(pattern, pg_conn, agency_id)


def test_uniform_delays_pattern_aggregates_match_hand_computed_values(tmp_path, pg_conn, agency_id, ch_client):
    pattern = uniform_delays()
    run_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client)
    _assert_all(pattern, pg_conn, agency_id)


def test_outlier_spike_pattern_aggregates_match_hand_computed_values(tmp_path, pg_conn, agency_id, ch_client):
    pattern = outlier_spike()
    run_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client)
    _assert_all(pattern, pg_conn, agency_id)


def test_null_delays_pattern_aggregates_match_hand_computed_values(tmp_path, pg_conn, agency_id, ch_client):
    pattern = null_delays()
    run_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client)
    _assert_all(pattern, pg_conn, agency_id)


def test_all_named_patterns_are_registered_in_all_patterns():
    """Growability guard: a future 4th pattern function added to
    `tests.fixtures.synthetic_gtfs` but forgotten from `ALL_PATTERNS` would
    silently stay invisible to any item-22/23 test that loops over
    `ALL_PATTERNS` instead of naming patterns individually. Pin the exact
    set this item shipped with so that omission fails loudly instead."""
    names = {pattern_fn().name for pattern_fn in ALL_PATTERNS}
    assert names == {"uniform_delays", "outlier_spike", "null_delays"}
