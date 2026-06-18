"""Integration test for scripts/drift_check.sh against the throwaway :5544 DB."""

import os
import subprocess
from pathlib import Path

import psycopg2
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "drift_check.sh"


@pytest.fixture
def stale_agency(apply_schema):
    """An agency with a completed-day `updates` row but no `agg_route_daily`
    coverage → check_aggs flags it stale. Cleaned up so other tests stay green."""
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
    try:
        yield aid
    finally:
        cur.execute("DELETE FROM updates WHERE agency_id=%s", (aid,))
        cur.execute("DELETE FROM agencies WHERE agency_id=%s", (aid,))
        conn.close()


def test_exit0_and_reports_both_checks_on_current_db(apply_schema):
    # :5544 is freshly migrated with current aggregates -> both checks pass.
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["DATABASE_URL"]},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "check_migrations" in out
    assert "check_aggs" in out


def test_exit1_when_aggs_stale(stale_agency):
    # A stale agency present -> check_aggs nonzero -> wrapper exits 1 (drift found),
    # which must be distinct from the exit-2 misconfig code.
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["DATABASE_URL"]},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "PROBLEM" in out


def test_exit2_when_database_url_unset():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "DATABASE_URL" in (r.stdout + r.stderr)
