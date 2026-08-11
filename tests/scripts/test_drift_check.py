"""Integration test for scripts/drift_check.sh against the throwaway :5544 DB."""

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import clickhouse_connect
import psycopg2
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "drift_check.sh"

# check_aggs now also builds a ClickHouse client (pipeline.clickhouse.get_client)
# for the freshness check's live-side lookup. Point it at the throwaway
# `make ch-test` instance so the subprocess doesn't KeyError on missing
# CLICKHOUSE_* config — mirrors how DATABASE_URL below points at :5544
# instead of the real dev DB.
_CH_TEST_ENV = {
    "CLICKHOUSE_HOST": "localhost",
    "CLICKHOUSE_PORT": os.environ.get("CLICKHOUSE_TEST_PORT", "8124"),
    "CLICKHOUSE_USER": "transit",
    "CLICKHOUSE_PASSWORD": "transit",
    "CLICKHOUSE_DATABASE": "transit_test",
}

# Both subprocess-driven tests below invoke `check_aggs`, which now builds a
# real ClickHouse client for the freshness check's live-side lookup — they
# need `make ch-test` up, same as the other ClickHouse-integration tests.
_ch_integration = pytest.mark.skipif(
    os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test` (RUN_CH_INTEGRATION=1)"
)


def _ch_test_client():
    return clickhouse_connect.get_client(
        host=_CH_TEST_ENV["CLICKHOUSE_HOST"],
        port=int(_CH_TEST_ENV["CLICKHOUSE_PORT"]),
        username=_CH_TEST_ENV["CLICKHOUSE_USER"],
        password=_CH_TEST_ENV["CLICKHOUSE_PASSWORD"],
        database=_CH_TEST_ENV["CLICKHOUSE_DATABASE"],
    )


@pytest.fixture
def stale_agency(apply_schema):
    """An agency with a completed-day `updates` row but no `agg_route_daily`
    coverage → check_aggs flags it stale. Cleaned up so other tests stay green.

    check_agg_freshness's live-side max now comes from ClickHouse, not
    Postgres, so the completed-day row must land there too (the Postgres
    `updates` insert stays only because it's otherwise harmless and mirrors
    what a real agency would have)."""
    from pipeline.clickhouse import insert_updates

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO agencies (agency_name, feed_url) VALUES ('drift-test','x') RETURNING agency_id")
    aid = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
        "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
        "(%s, 'drift.pb', now() - interval '2 days', 'T1', '平日', '11:00', 'R1', 1, 60)",
        (aid,),
    )
    ch_client = _ch_test_client()
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    insert_updates(
        ch_client,
        aid,
        [("drift.pb", two_days_ago, "T1", "平日", "11:00", "R1", 1, 60)],
    )
    try:
        yield aid
    finally:
        cur.execute("DELETE FROM updates WHERE agency_id=%s", (aid,))
        cur.execute("DELETE FROM agencies WHERE agency_id=%s", (aid,))
        conn.close()
        # No ClickHouse-side cleanup: `agencies_agency_id_seq` never repeats
        # within a test session, so a leftover row under this now-deleted
        # agency_id can't leak into another test's assertions. ClickHouse
        # row deletion is an async mutation — skipping it keeps this fixture
        # fast and simple.
        ch_client.close()


@_ch_integration
def test_exit0_and_reports_both_checks_on_current_db(apply_schema):
    # :5544 is freshly migrated with current aggregates -> both checks pass.
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["DATABASE_URL"], **_CH_TEST_ENV},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "check_migrations" in out
    assert "check_aggs" in out


@_ch_integration
def test_exit1_when_aggs_stale(stale_agency):
    # A stale agency present -> check_aggs nonzero -> wrapper exits 1 (drift found),
    # which must be distinct from the exit-2 misconfig code.
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["DATABASE_URL"], **_CH_TEST_ENV},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "PROBLEM" in out


def test_exit2_when_database_url_unset():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "DATABASE_URL" in (r.stdout + r.stderr)
