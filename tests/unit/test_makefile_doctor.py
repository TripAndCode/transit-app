"""`make doctor`'s `.env` checks, run for real against a scratch checkout.

The SSO and ClickHouse lines count matching `.env` lines with `grep -c`,
which prints no count at all when the file is absent, so they are exercised
with and without a `.env` rather than read as text. Each run copies the
Makefile into `tmp_path`, so no real `.env` is ever read or written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SSO_VARS = (
    "SESSION_SIGNING_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
)
CLICKHOUSE_VARS = ("CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE")


def _doctor(checkout: Path, env_file: str | None) -> list[str]:
    shutil.copy(REPO_ROOT / "Makefile", checkout / "Makefile")
    if env_file is not None:
        (checkout / ".env").write_text(env_file)
    env = {k: v for k, v in os.environ.items() if k not in SSO_VARS + CLICKHOUSE_VARS}
    result = subprocess.run(["make", "-s", "doctor"], cwd=checkout, env=env, capture_output=True, text=True, check=True)
    return [line.strip() for line in result.stdout.splitlines()]


def _line(lines: list[str], prefix: str) -> str:
    matches = [line for line in lines if line.startswith(prefix)]
    assert len(matches) == 1, f"expected one `{prefix}` line, got {matches} in:\n" + "\n".join(lines)
    return matches[0]


def _assignments(names: tuple[str, ...]) -> str:
    return "".join(f"{name}=value-{i}\n" for i, name in enumerate(names))


NO_SSO = "SSO env: none set (anonymous-only)"
NO_CLICKHOUSE = "CLICKHOUSE env: PARTIAL (0/3) — `make ch-bootstrap` will fail"


@pytest.mark.parametrize(
    ("env_file", "sso", "clickhouse"),
    [
        pytest.param(None, NO_SSO, NO_CLICKHOUSE, id="no-env-file"),
        pytest.param("", NO_SSO, NO_CLICKHOUSE, id="empty-env-file"),
        pytest.param(
            _assignments(SSO_VARS + CLICKHOUSE_VARS),
            "SSO env: all 5 set (login enabled)",
            "CLICKHOUSE env: all 3 set",
            id="all-set",
        ),
        pytest.param(
            _assignments(SSO_VARS[:2]), "SSO env: PARTIAL (2/5) — startup will fail", NO_CLICKHOUSE, id="some-set"
        ),
    ],
)
def test_doctor_env_counts(tmp_path, env_file, sso, clickhouse):
    lines = _doctor(tmp_path, env_file)
    assert _line(lines, "SSO env:") == sso
    assert _line(lines, "CLICKHOUSE env:") == clickhouse


def _doctor_with_docker(checkout: Path, services: str, exit_codes: str) -> list[str]:
    """Run doctor with a stand-in `docker` that answers `ps --services` and
    `ps --format` from the given text."""
    shutil.copy(REPO_ROOT / "Makefile", checkout / "Makefile")
    bin_dir = checkout / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        f"  *--services*) printf '%s' '{services}' ;;\n"
        f"  *--format*) printf '%s' '{exit_codes}' ;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in SSO_VARS + CLICKHOUSE_VARS}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    result = subprocess.run(["make", "-s", "doctor"], cwd=checkout, env=env, capture_output=True, text=True, check=True)
    return [line.strip() for line in result.stdout.splitlines()]


def test_doctor_names_the_guide_when_the_standalone_services_are_not_running(tmp_path):
    lines = _doctor_with_docker(tmp_path, "", "")
    assert "see docs/deploy-standalone.en.md" in _line(lines, "app/poller not running")
    assert not [line for line in lines if "migrate" in line]


def test_doctor_reports_running_standalone_services(tmp_path):
    lines = _doctor_with_docker(tmp_path, "app\npoller\n", "migrate 0\n")
    assert _line(lines, "app and poller:") == "app and poller: running"


def test_doctor_points_at_the_migrate_log_when_the_schema_step_failed(tmp_path):
    lines = _doctor_with_docker(tmp_path, "", "migrate 1\n")
    assert "logs migrate" in _line(lines, "schema step (migrate) FAILED")
