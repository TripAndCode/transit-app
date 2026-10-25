"""Internal cron endpoint — secret enforcement and 202-fast-return."""

import os
from unittest.mock import MagicMock, patch

import httpx
import psycopg2
import pytest
from httpx import ASGITransport

from pipeline.db import DEFAULT_DATABASE_URL
from tests.conftest import _test_pool

DATABASE_URL = os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


@pytest.fixture
async def cron_app(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    yield app
    await pool.close()


@pytest.fixture
def two_agencies(apply_schema):
    """One active, one soft-deleted agency. Cleaned up after the test."""
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Cron Active", "http://cron-active.example.com"),
        )
        active_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES (%s, %s, now()) RETURNING agency_id",
            ("Cron Deleted", "http://cron-deleted.example.com"),
        )
        deleted_id = cur.fetchone()[0]
    conn.commit()
    try:
        yield active_id, deleted_id
    finally:
        with conn.cursor() as cur:
            # The run rows first: `pipeline_runs.agency_id` references
            # `agencies`, and the tests below drive the real sweep, which
            # records one row per agency it touches. Leaving them behind
            # would make this DELETE fail and strand both agencies, so the
            # next run of this file collides on `feed_url`.
            cur.execute("DELETE FROM pipeline_runs WHERE agency_id IN (%s, %s)", (active_id, deleted_id))
            cur.execute("DELETE FROM agencies WHERE agency_id IN (%s, %s)", (active_id, deleted_id))
        conn.commit()
        conn.close()


@pytest.mark.anyio
async def test_cron_ingest_rejects_missing_header(cron_app, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "shh")
    transport = ASGITransport(app=cron_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/internal/cron/ingest")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_cron_ingest_rejects_wrong_secret(cron_app, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "shh")
    transport = ASGITransport(app=cron_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/internal/cron/ingest", headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_cron_ingest_503_when_secret_unset(cron_app, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    transport = ASGITransport(app=cron_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/internal/cron/ingest", headers={"X-Cron-Secret": "anything"})
    assert r.status_code == 503


@pytest.mark.anyio
async def test_cron_ingest_returns_202_and_schedules_work(cron_app, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "shh")
    transport = ASGITransport(app=cron_app)
    # The background task spawns subprocess work we don't want during tests —
    # patch it to a no-op and just verify the dispatch.
    with patch("api.routers.internal._run_ingest_and_analyze") as fake:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/internal/cron/ingest", headers={"X-Cron-Secret": "shh"})
        assert r.status_code == 202
        assert r.json() == {"status": "started"}
        # BackgroundTasks runs after the response is returned by the AsyncClient
        # client context exits, so by the time we're here the task has executed.
        fake.assert_called_once()


def test_run_ingest_and_analyze_skips_deleted_agency(two_agencies, monkeypatch):
    """The actual cron work loop must not ingest/analyze a soft-deleted agency."""
    active_id, deleted_id = two_agencies
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    from api.routers.internal import _run_ingest_and_analyze

    with (
        patch("pipeline.ingest.ingest_live") as fake_ingest,
        patch("pipeline.analyze.analyze") as fake_analyze,
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.promote.promote_closed_days", return_value=0) as fake_promote,
    ):
        _run_ingest_and_analyze()

    ingested_ids = [c.args[0] for c in fake_ingest.call_args_list]
    analyzed_ids = [c.args[0] for c in fake_analyze.call_args_list]
    assert active_id in ingested_ids
    assert deleted_id not in ingested_ids
    assert active_id in analyzed_ids
    assert deleted_id not in analyzed_ids
    assert deleted_id not in [c.args[0] for c in fake_promote.call_args_list]


def test_run_ingest_and_analyze_skips_when_already_running(two_agencies, monkeypatch):
    """A concurrently-running invocation (a double cron poke, or a retried
    BackgroundTask on POST /internal/cron/ingest) must skip entirely rather
    than ingest_live-ing every agency's feed twice -- ClickHouse has no
    ON CONFLICT DO NOTHING to absorb the resulting duplicate poll beyond the
    single-file bounded check ingest_live already does on its own file_name.

    Holds the same advisory lock _run_ingest_and_analyze takes (now shared
    with gtfs_pipeline.py's CLI commands via pipeline.locks), on a separate
    connection, to simulate the concurrent run."""
    _active_id, _deleted_id = two_agencies
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    from api.routers.internal import _run_ingest_and_analyze
    from pipeline.locks import INGEST_ANALYZE_LOCK_KEY

    holder = psycopg2.connect(DATABASE_URL)
    holder.autocommit = True
    with holder.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        assert cur.fetchone()[0] is True

    try:
        with (
            patch("pipeline.ingest.ingest_live") as fake_ingest,
            patch("pipeline.analyze.analyze") as fake_analyze,
            patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
            patch("pipeline.weather.ingest_weather") as fake_weather,
        ):
            _run_ingest_and_analyze()

        fake_ingest.assert_not_called()
        fake_analyze.assert_not_called()
        # A lock miss must skip the weather pass too -- it is gated on the
        # same "did this poke actually do anything" check as ingest/analyze.
        fake_weather.assert_not_called()
    finally:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        holder.close()

    # The lock must actually be released once the holder is gone -- not
    # wedged forever, which would silently skip every subsequent cron poke.
    # check_agg_freshness patched explicitly (matching
    # test_run_ingest_and_analyze_skips_deleted_agency above) rather than
    # left to run for real against a bare MagicMock ch_client, which holds no
    # real freshness data; that check is unrelated to what this test actually
    # verifies (lock release).
    with (
        patch("pipeline.ingest.ingest_live") as fake_ingest,
        patch("pipeline.analyze.analyze") as fake_analyze,
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.weather.ingest_weather") as fake_weather,
    ):
        _run_ingest_and_analyze()

    assert fake_ingest.call_count > 0
    assert fake_analyze.call_count > 0
    # A successful poke (lock acquired, agencies found) must still run the
    # weather pass -- only a skipped poke omits it.
    fake_weather.assert_called_once()


def test_weather_ingest_runs_only_after_the_advisory_lock_is_released(two_agencies, monkeypatch):
    """The weather pass must open its own connection after the ingest+analyze
    advisory lock's connection has already closed -- never share it. Sharing
    it would let a slow/trickling weather fetch hold the lock open, and every
    cron poke arriving meanwhile would take the "already in flight" path,
    dropping live GTFS-RT polls that can't be recovered later (see
    pipeline.weather.CRON_INGEST_BUDGET_SEC)."""
    _active_id, _deleted_id = two_agencies
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    from api.routers.internal import _run_ingest_and_analyze
    from pipeline.locks import INGEST_ANALYZE_LOCK_KEY

    lock_was_free_during_weather = None

    def fake_ingest_weather(conn, **kwargs):
        nonlocal lock_was_free_during_weather
        # A probe on a brand-new connection can only acquire the lock if the
        # one _run_ingest_and_analyze held has already been released.
        probe = psycopg2.connect(DATABASE_URL)
        probe.autocommit = True
        try:
            with probe.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
                lock_was_free_during_weather = cur.fetchone()[0]
            if lock_was_free_during_weather:
                with probe.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        finally:
            probe.close()
        return (0, 0, [])

    with (
        patch("pipeline.ingest.ingest_live") as fake_ingest,
        patch("pipeline.analyze.analyze") as fake_analyze,
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.weather.ingest_weather", side_effect=fake_ingest_weather) as fake_weather,
    ):
        _run_ingest_and_analyze()

    # >0 rather than assert_called_once: the shared test database may carry
    # other already-seeded agencies alongside this fixture's own, so more
    # than one ingest_live/analyze call is a valid outcome -- what this test
    # verifies is the lock state during the (single) weather pass below.
    assert fake_ingest.call_count > 0
    assert fake_analyze.call_count > 0
    fake_weather.assert_called_once()
    assert lock_was_free_during_weather is True


@pytest.fixture(autouse=True)
def _no_evaluation_ingest(monkeypatch):
    """Keeps every test here off the network: a configured ML store would
    otherwise send the evaluation step's listing to the fake endpoint. A test
    about that step replaces this stub with its own."""
    import api.routers.internal as internal_module

    monkeypatch.setattr(internal_module, "ingest_evaluations", lambda conn, s3, bucket: 0)


def test_the_sweep_ingests_the_ml_forecast_after_analyze(monkeypatch):
    import api.routers.internal as internal_module

    calls = []

    def fake_ingest_forecast(conn, s3_client, bucket):
        calls.append(bucket)
        return 0

    monkeypatch.setattr(internal_module, "ingest_forecast", fake_ingest_forecast)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "test")

    # Exercises only the forecast step in isolation, not the whole sweep
    # (which needs ClickHouse/live agencies already covered by this file's
    # other tests) -- the fake replaces ingest_forecast, so the connection
    # object passed through is never actually used.
    internal_module._ingest_ml_forecast(object())
    assert calls == ["transit-ml"]


def test_a_forecast_ingest_failure_does_not_raise(monkeypatch):
    import api.routers.internal as internal_module

    def raising_ingest_forecast(conn, s3_client, bucket):
        raise RuntimeError("boom")

    monkeypatch.setattr(internal_module, "ingest_forecast", raising_ingest_forecast)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "test")

    internal_module._ingest_ml_forecast(object())  # must not raise


def test_a_store_missing_its_keys_is_skipped_quietly_without_connecting(monkeypatch, caplog):
    """The pre-connect gate checks the same variables the S3 client needs:
    a deployment with only the bucket and endpoint set is unconfigured, not a
    sweep-by-sweep failure."""
    import logging

    import api.routers.internal as internal_module

    calls = []
    monkeypatch.setattr(internal_module, "ingest_forecast", lambda *args: calls.append(args) or 0)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.delenv("ML_STORE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ML_STORE_SECRET_ACCESS_KEY", raising=False)
    connects = []
    monkeypatch.setattr(psycopg2, "connect", lambda *args, **kwargs: connects.append(args) or MagicMock())

    with caplog.at_level(logging.ERROR):
        internal_module._run_ml_forecast_ingest("postgresql://unused")
        internal_module._ingest_ml_forecast(object())

    assert connects == []
    assert calls == []
    assert "ml_forecast ingest failed" not in caplog.text


def test_ml_forecast_ingest_runs_only_for_an_unscoped_ingest_sweep(two_agencies, monkeypatch):
    """The published forecast is fleet-wide (one R2 object, not one per
    agency) -- a single-agency admin reanalyze must not trigger a
    fleet-wide R2 fetch and upsert."""
    active_id, _deleted_id = two_agencies
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    from api.routers.internal import _ingest_and_analyze_sweep

    with (
        patch("pipeline.ingest.ingest_live"),
        patch("pipeline.analyze.analyze"),
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.promote.promote_closed_days", return_value=0),
        patch("api.routers.internal._run_weather_ingest"),
        patch("api.routers.internal._run_ml_forecast_ingest") as fake_ml_ingest,
    ):
        _ingest_and_analyze_sweep(DATABASE_URL, agency_ids=[active_id])
        assert fake_ml_ingest.call_count == 0

        _ingest_and_analyze_sweep(DATABASE_URL)
        assert fake_ml_ingest.call_count == 1


def test_ml_forecast_ingest_runs_only_after_the_advisory_lock_is_released(two_agencies, monkeypatch):
    """Same reasoning as weather's own lock-release test: ingest_forecast
    must run on a fresh connection opened after the ingest+analyze advisory
    lock's own connection has already closed, never while it is still held."""
    _active_id, _deleted_id = two_agencies
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "test")

    from api.routers.internal import _run_ingest_and_analyze
    from pipeline.locks import INGEST_ANALYZE_LOCK_KEY

    lock_was_free_during_ml_forecast = None

    def fake_ingest_forecast(conn, s3_client, bucket):
        nonlocal lock_was_free_during_ml_forecast
        probe = psycopg2.connect(DATABASE_URL)
        probe.autocommit = True
        try:
            with probe.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
                lock_was_free_during_ml_forecast = cur.fetchone()[0]
            if lock_was_free_during_ml_forecast:
                with probe.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        finally:
            probe.close()
        return 0

    with (
        patch("pipeline.ingest.ingest_live"),
        patch("pipeline.analyze.analyze"),
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.weather.ingest_weather", return_value=(0, 0, [])),
        patch("api.routers.internal.ingest_forecast", side_effect=fake_ingest_forecast) as fake_ml,
    ):
        _run_ingest_and_analyze()

    fake_ml.assert_called_once()
    assert lock_was_free_during_ml_forecast is True


def _configured_store(monkeypatch):
    for name, value in (
        ("ML_STORE_BUCKET", "transit-ml"),
        ("ML_STORE_ENDPOINT", "https://example.r2"),
        ("ML_STORE_ACCESS_KEY_ID", "test"),
        ("ML_STORE_SECRET_ACCESS_KEY", "test"),
    ):
        monkeypatch.setenv(name, value)


def test_the_sweep_also_ingests_the_evaluation_summaries(monkeypatch):
    import api.routers.internal as internal_module

    calls = []
    monkeypatch.setattr(internal_module, "ingest_forecast", lambda conn, s3, bucket: calls.append("forecast") or 0)
    monkeypatch.setattr(
        internal_module, "ingest_evaluations", lambda conn, s3, bucket: calls.append("evaluations") or 0
    )
    _configured_store(monkeypatch)
    internal_module._ingest_ml_forecast(object())
    assert calls == ["forecast", "evaluations"]


def test_a_forecast_step_that_raises_does_not_skip_the_evaluations(monkeypatch):
    import api.routers.internal as internal_module

    calls = []

    def boom(conn, s3, bucket):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(internal_module, "ingest_forecast", boom)
    monkeypatch.setattr(
        internal_module, "ingest_evaluations", lambda conn, s3, bucket: calls.append("evaluations") or 0
    )
    _configured_store(monkeypatch)
    internal_module._ingest_ml_forecast(object())  # must not raise
    assert calls == ["evaluations"]
