"""Integration test for scripts/drift_check.sh against the throwaway :5544 DB."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "drift_check.sh"


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


def test_exit2_when_database_url_unset():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "DATABASE_URL" in (r.stdout + r.stderr)
