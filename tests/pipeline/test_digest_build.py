"""DB-backed tests for build_digest."""

from datetime import date
from unittest.mock import patch

import pytest

from pipeline.digest.build import build_digest

DAY = date(2026, 4, 2)


class _EmptyChClient:
    """Stub ClickHouse client: every query behaves like an agency with no
    `updates` rows at all (max_captured_at → None), matching this file's old
    Postgres-only fixtures which never seeded the live `updates` table
    either — so is_stale stays False, same as before the ClickHouse port.

    `result_rows = []` (not `[(None,)]`): `pipeline.clickhouse.max_captured_at`
    /`max_captured_at_before` now query `ORDER BY captured_at DESC LIMIT 1`
    instead of `maxOrNull(captured_at)` (index-served off the `updates` sort
    key instead of a full aggregate scan) — a query with no matching rows
    returns an EMPTY result set under `ORDER BY ... LIMIT 1`, unlike a
    no-GROUP-BY aggregate like `maxOrNull`, which always returns exactly one
    row (value `NULL`) even over zero input rows."""

    def query(self, *_args, **_kwargs):
        class _Result:
            result_rows = []

        return _Result()


class _FailingChClient:
    """Stub ClickHouse client whose query() always raises — simulates
    check_agg_freshness's live-day probe failing mid-query (ClickHouse
    unreachable, timeout, etc.)."""

    def query(self, *_args, **_kwargs):
        raise RuntimeError("simulated ClickHouse failure")


@pytest.fixture(autouse=True)
def _stub_ch_client():
    """build_digest() now builds its own ClickHouse client (via
    pipeline.clickhouse.get_client()) to feed check_agg_freshness's live-day
    lookup. This test file is Postgres-only and pre-dates ClickHouse, so
    stub the client out rather than requiring `make ch-test` for every
    digest test — none of them assert on ClickHouse-sourced staleness."""
    with patch("pipeline.digest.build.get_client", return_value=_EmptyChClient()):
        yield


def _seed(pg_conn, agency_id):
    """One degraded route (anomaly) + one normal route, with baselines + feed-health."""
    with pg_conn.cursor() as cur:
        for route, avg_sec in (("44372", 480), ("12", 120)):
            cur.execute(
                "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
                "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
                "VALUES (%s, %s, %s, '平日', %s, %s, 10, 50, %s, %s)",
                (agency_id, DAY, route, avg_sec, avg_sec * 2, "2026-04-02T11:37:00+09:00", avg_sec * 50),
            )
        for route, avg_min, p90_min in (("44372", 3.0, 5.0), ("12", 2.0, 4.0)):
            cur.execute(
                "INSERT INTO agg_route_stats (agency_id, route_code, service_type, "
                "avg_min, p50_min, p90_min, late_5min_plus, on_time_pct, late5_pct, samples, sum_delay_sec) "
                "VALUES (%s, %s, '平日', %s, %s, %s, 0, 90.0, 1.0, 500, %s)",
                (agency_id, route, avg_min, avg_min, p90_min, round(avg_min * 60 * 500)),
            )
        cur.execute(
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) VALUES (%s, %s, 3400, 12)",
            (agency_id, DAY),
        )
    pg_conn.commit()


def test_build_movers_headline_and_footer(pg_conn, agency_id):
    _seed(pg_conn, agency_id)
    data = build_digest(pg_conn, DAY)
    assert data.target_day == DAY
    assert data.network_avg_delay_min == 5.0
    section = next(s for s in data.sections if s.agency_id == agency_id)
    assert section.has_data is True
    assert section.avg_delay_min == 5.0
    assert [m.route_code for m in section.movers] == ["44372"]
    assert section.movers[0].deviation_min == 5.0
    assert section.raw_samples == 3400
    assert section.clamp_count == 12
    assert section.is_stale is False


def test_build_empty_day_marks_no_data(pg_conn, agency_id):
    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)
    assert section.has_data is False
    assert section.avg_delay_min is None
    assert data.network_avg_delay_min is None


def test_build_digest_excludes_deleted_agency(pg_conn, agency_id):
    _seed(pg_conn, agency_id)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES (%s, %s, now()) RETURNING agency_id",
            ("Deleted Digest Agency", "http://deleted-digest.example.com"),
        )
        deleted_id = cur.fetchone()[0]
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)

    assert deleted_id not in [s.agency_id for s in data.sections]
    assert agency_id in [s.agency_id for s in data.sections]


def _insert_daily(cur, agency_id, route, service_type, avg_sec, samples, sum_delay_sec=None):
    """Seed one agg_route_daily row. ``sum_delay_sec`` defaults to the exact
    ``avg_sec * samples`` reconstruction (matching what analyze() would emit
    when ``avg_sec`` is itself exact); pass it explicitly to test rounding
    divergence between the two."""
    if sum_delay_sec is None:
        sum_delay_sec = avg_sec * samples
    cur.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES (%s, %s, %s, %s, %s, %s, 10, %s, %s, %s)",
        (
            agency_id,
            DAY,
            route,
            service_type,
            avg_sec,
            avg_sec * 2,
            samples,
            "2026-04-02T11:37:00+09:00",
            sum_delay_sec,
        ),
    )


def _insert_stats(cur, agency_id, route, service_type, avg_min, p90_min, samples=500, sum_delay_sec=None):
    """Seed one agg_route_stats row. ``sum_delay_sec`` defaults to the exact
    ``avg_min * 60 * samples`` reconstruction (matching what analyze() would
    emit when ``avg_min`` is itself exact) so _ROUTE_BASELINE_SQL's FILTERed
    pooling has a real value to sum, not a NULL that would drop the route to
    no_baseline; pass it explicitly to test rounding divergence."""
    if sum_delay_sec is None:
        sum_delay_sec = round(avg_min * 60 * samples)
    cur.execute(
        "INSERT INTO agg_route_stats (agency_id, route_code, service_type, "
        "avg_min, p50_min, p90_min, late_5min_plus, on_time_pct, late5_pct, samples, sum_delay_sec) "
        "VALUES (%s, %s, %s, %s, %s, %s, 0, 90.0, 1.0, %s, %s)",
        (agency_id, route, service_type, avg_min, avg_min, p90_min, samples, sum_delay_sec),
    )


def test_multi_service_type_dedups_to_one_mover(pg_conn, agency_id):
    """A route with two service_types on the same day yields exactly ONE mover,
    with a sample-weighted blended average."""
    with pg_conn.cursor() as cur:
        # Same route 44372, two service_types on the same day.
        _insert_daily(cur, agency_id, "44372", "平日", 480, 50)
        _insert_daily(cur, agency_id, "44372", "土曜", 600, 30)
        # Baseline per (route, service_type) — well below today's avg → anomaly.
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
        _insert_stats(cur, agency_id, "44372", "土曜", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    # Exactly one mover for the route, not two.
    matching = [m for m in section.movers if m.route_code == "44372"]
    assert len(matching) == 1
    # round((480*50 + 600*30) / 80) = 525 sec; round(525 / 60, 1) = 8.8.
    assert matching[0].avg_delay_min == 8.8


def test_movers_tie_break_is_deterministic(pg_conn, agency_id):
    """Two routes tied on deviation_min must sort by route_code, ascending,
    regardless of insertion order, since `route_entries` gives no ordering
    guarantee of its own. Insert route "9" before "1" so an unguarded sort's
    tie order would depend on insertion/scan order rather than route_code."""
    with pg_conn.cursor() as cur:
        for route in ("9", "1"):
            _insert_daily(cur, agency_id, route, "平日", 480, 50)
            _insert_stats(cur, agency_id, route, "平日", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    codes = [m.route_code for m in section.movers if m.route_code in ("9", "1")]
    assert codes == ["1", "9"]
    # Confirm they're genuinely tied, not incidentally distinct.
    devs = {m.route_code: m.deviation_min for m in section.movers if m.route_code in ("9", "1")}
    assert devs["1"] == devs["9"]


def test_top_5_cap_keeps_highest_deviation(pg_conn, agency_id):
    """Six+ anomaly routes → only the top 5 by deviation are kept, sorted desc."""
    # avg_sec per route well above baseline p90 (300s) → all anomalies.
    routes = [
        ("r1", 360),
        ("r2", 420),
        ("r3", 480),
        ("r4", 540),
        ("r5", 600),
        ("r6", 660),
        ("r7", 720),
    ]
    with pg_conn.cursor() as cur:
        for route, avg_sec in routes:
            _insert_daily(cur, agency_id, route, "平日", avg_sec, 50)
            _insert_stats(cur, agency_id, route, "平日", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    assert len(section.movers) == 5
    devs = [m.deviation_min for m in section.movers]
    assert devs == sorted(devs, reverse=True)
    # The 5 highest-deviation routes are r7..r3 (highest avgs).
    assert [m.route_code for m in section.movers] == ["r7", "r6", "r5", "r4", "r3"]


def test_multi_agency_network_is_sample_weighted(pg_conn, agency_id):
    """network_avg_delay_min weights by samples across agencies (not mean-of-means)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) "
            "VALUES ('digest agency 2','http://example.test/dfeed2') RETURNING agency_id"
        )
        agency_id2 = cur.fetchone()[0]
        # agency 1: avg 480s over 50 samples; agency 2: avg 240s over 150 samples.
        _insert_daily(cur, agency_id, "44372", "平日", 480, 50)
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
        _insert_daily(cur, agency_id2, "99", "平日", 240, 150)
        _insert_stats(cur, agency_id2, "99", "平日", 2.0, 4.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)

    # (480*50 + 240*150) / (50+150) = 60000/200 = 300 sec = 5.0 min.
    # mean-of-section-means would be (8.0 + 4.0)/2 = 6.0, so 5.0 proves weighting.
    assert data.network_avg_delay_min == 5.0


def test_low_confidence_anomaly_caps_at_watch(pg_conn, agency_id):
    """A thin anomaly route (samples < 30) is low_confidence and capped at watch."""
    with pg_conn.cursor() as cur:
        # avg 480s >> baseline p90 (300s) → would be anomaly, but only 10 samples.
        _insert_daily(cur, agency_id, "44372", "平日", 480, 10)
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)
    mover = next(m for m in section.movers if m.route_code == "44372")
    assert mover.low_confidence is True
    assert mover.bucket == "watch"


def test_null_service_route_gets_route_grain_baseline(pg_conn, agency_id):
    """A NULL-service route (stored as '' in agg_route_daily) finds the route's
    overall baseline (aggregated across service_types in agg_route_stats) and is
    triaged as a mover — proving '' rows are no longer dropped by the baseline join."""
    with pg_conn.cursor() as cur:
        # Daily row with empty-string service_type (the COALESCE'd NULL case).
        _insert_daily(cur, agency_id, "44372", "", 480, 50)
        # Baseline only under a TYPED service_type ('平日'); no '' row exists.
        # day avg 480s > baseline p90 (300s) → anomaly once the route baseline matches.
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    matching = [m for m in section.movers if m.route_code == "44372"]
    assert len(matching) == 1
    assert matching[0].bucket == "anomaly"
    assert matching[0].baseline_avg_min == 3.0


def test_route_baseline_sql_p90_pooling_ignores_null_p90_rows(pg_conn, agency_id):
    """_ROUTE_BASELINE_SQL's base_p90_min must not be diluted by a
    contributing service_type whose own p90_min is null -- not something
    `analyze()`'s own SQL can currently produce for a live group, but a stale
    pre-rebuild row or, as here, a directly-seeded fixture can still exercise
    this shape. SUM(p90_min * samples) silently skips a
    null numerator term, but the denominator must be FILTERed the same way,
    or that row's samples still count against a p90 it contributed nothing
    to -- biasing the pooled figure down."""
    from pipeline.digest.build import _ROUTE_BASELINE_SQL

    with pg_conn.cursor() as cur:
        # Thin/degenerate group: real avg, but a null p90 (samples still count).
        _insert_stats(cur, agency_id, "R1", "平日", 2.0, None, samples=3)
        # Healthy group: real avg and p90.
        _insert_stats(cur, agency_id, "R1", "土日", 4.0, 10.0, samples=500)
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(_ROUTE_BASELINE_SQL, {"aid": agency_id})
        (route_code, base_avg_min, base_p90_min) = cur.fetchone()

    assert route_code == "R1"
    # base_avg_min comes back as decimal.Decimal (the SQL's ::numeric cast);
    # pytest.approx can't subtract a Decimal from a float expected value, so
    # cast to float first, matching this file's other base_avg_min assertion
    # (test_route_baseline_sql_pools_exact_sum_delay_sec_not_rounded_avg_min).
    assert float(base_avg_min) == pytest.approx((2.0 * 3 + 4.0 * 500) / 503)
    # Must equal the healthy group's own p90 exactly (the only contributor) --
    # NOT diluted to 10.0*500/503 ~= 9.94 by including the null-p90 row's
    # samples in the denominator.
    assert base_p90_min == pytest.approx(10.0)


def test_route_baseline_sql_pools_exact_sum_delay_sec_not_rounded_avg_min(pg_conn, agency_id):
    """_ROUTE_BASELINE_SQL's base_avg_min must pool each service_type's EXACT
    sum_delay_sec, not re-weight each service_type's already-rounded avg_min.

    Two service_types for the same route: 3 samples whose raw-seconds sum is
    124 (analyze() would round that group's own avg_min to 0.69 min) and 7
    samples whose raw-seconds sum is 700 (rounds to 1.67 min). Pooling the
    exact sums gives (124+700)/10/60 = 1.37333... min; re-weighting the
    rounded 0.69/1.67 instead (the pre-fix pattern) gives
    (0.69*3 + 1.67*7)/10 = 1.376 min -- these round to a different whole
    second (82 vs 83) once converted the way build_digest actually consumes
    this figure, proving the two methods diverge."""
    from pipeline.digest.build import _ROUTE_BASELINE_SQL

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, samples, sum_delay_sec) "
            "VALUES (%s, 'R1', '平日', 0.69, 3, 124)",
            (agency_id,),
        )
        cur.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, samples, sum_delay_sec) "
            "VALUES (%s, 'R1', '土日', 1.67, 7, 700)",
            (agency_id,),
        )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(_ROUTE_BASELINE_SQL, {"aid": agency_id})
        (route_code, base_avg_min, _base_p90_min) = cur.fetchone()

    assert route_code == "R1"
    exact_sec = round(float(base_avg_min) * 60)
    buggy_sec = round((0.69 * 3 + 1.67 * 7) / 10 * 60)
    assert exact_sec == 82
    assert buggy_sec == 83
    assert exact_sec != buggy_sec


def test_delta_min_only_compares_routes_with_a_baseline(pg_conn, agency_id):
    """delta_min must compare today's avg against the baseline using the SAME
    route population on both sides. Previously today's avg was weighted over
    ALL routes (including ones with no baseline at all) while the baseline
    side only counted routes that have one - a route with no baseline and a
    large delay could swing the headline delta even though the only route
    with a real historical baseline showed zero drift."""
    with pg_conn.cursor() as cur:
        # Route A: today == baseline (no real change).
        _insert_daily(cur, agency_id, "A", "平日", 300, 100)  # 5.0 min
        _insert_stats(cur, agency_id, "A", "平日", 5.0, 8.0)
        # Route B: no baseline row at all (under any service_type), big delay today.
        _insert_daily(cur, agency_id, "B", "平日", 900, 100)  # 15.0 min
        cur.execute(
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) VALUES (%s, %s, 100, 0)",
            (agency_id, DAY),
        )
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    assert section.avg_delay_min == 10.0  # unchanged: still the full-population headline
    assert section.baseline_avg_min == 5.0
    assert section.delta_min == 0.0  # NOT +5.0 - route A (the only one with a baseline) shows no drift


def test_build_digest_survives_ch_query_failure(pg_conn, agency_id):
    """A ClickHouse outage during check_agg_freshness's live-day probe must
    not kill the whole (Postgres-only) digest — only the advisory staleness
    flag should degrade to "not stale", every other (Postgres-sourced) field
    must still come through. staleness_known must flip to False so the
    renderer can tell "known fresh" apart from "staleness unknown" — see
    test_build_digest_ch_failure_does_not_render_as_fresh."""
    _seed(pg_conn, agency_id)

    with patch("pipeline.digest.build.get_client", return_value=_FailingChClient()):
        data = build_digest(pg_conn, DAY)

    section = next(s for s in data.sections if s.agency_id == agency_id)
    assert section.is_stale is False
    assert section.has_data is True
    assert section.avg_delay_min == 5.0
    assert data.network_avg_delay_min == 5.0
    assert data.staleness_known is False


def test_build_digest_survives_get_client_failure(pg_conn, agency_id):
    """get_client() itself raising (e.g. a missing ClickHouse env var) must
    not kill the whole digest either."""
    _seed(pg_conn, agency_id)

    def boom():
        raise KeyError("CLICKHOUSE_HOST")

    with patch("pipeline.digest.build.get_client", side_effect=boom):
        data = build_digest(pg_conn, DAY)

    section = next(s for s in data.sections if s.agency_id == agency_id)
    assert section.is_stale is False
    assert data.staleness_known is False


def test_build_digest_ch_failure_does_not_render_as_fresh(pg_conn, agency_id):
    """End-to-end: a ClickHouse outage must not produce an affirmative "all
    fresh" claim in the rendered Markdown — that's the digest's only output
    surface (gtfs_pipeline.cmd_digest just writes it to stdout; the
    _log.warning goes to logs a human digest-reader never sees). The
    rendered text must instead honestly signal that staleness is unknown."""
    from pipeline.digest.render import render_digest

    _seed(pg_conn, agency_id)

    with patch("pipeline.digest.build.get_client", return_value=_FailingChClient()):
        data = build_digest(pg_conn, DAY)

    for locale, fresh_phrase in (("ja", "全事業者最新"), ("en", "all agencies current")):
        out = render_digest(data, locale)
        assert fresh_phrase not in out
        assert "鮮度警告" not in out  # also not a false stale-warning
        assert "Freshness: aggregates lagging" not in out

    section = next(s for s in data.sections if s.agency_id == agency_id)
    assert section.avg_delay_min == 5.0  # Postgres-sourced field still comes through


def test_cmd_digest_prints_markdown(pg_conn, agency_id, capsys):
    import gtfs_pipeline

    _seed(pg_conn, agency_id)

    class _Args:
        day = "2026-04-02"
        locale = "ja"

    gtfs_pipeline.cmd_digest(_Args())
    out = capsys.readouterr().out
    assert "日次ダイジェスト 2026-04-02" in out
    assert "44372" in out
