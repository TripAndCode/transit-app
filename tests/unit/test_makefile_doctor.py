"""`make doctor`'s `.env` checks, run for real against a scratch checkout.

The sign-in lines read what Make exported from `.env`, and the ClickHouse
line counts matching `.env` lines with `grep -c`, which prints no count at
all when the file is absent, so both are exercised with and without a `.env`
rather than read as text. Each run copies the
Makefile into `tmp_path`, so no real `.env` is ever read or written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from api.sso import DEV_SIGNING_KEY, LOCAL_ADMIN_ENV, SIGNING_KEY_ENV, SSO_ENV

REPO_ROOT = Path(__file__).resolve().parents[2]

CLICKHOUSE_VARS = ("CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE")
# A pytest run started under `make … VAR=x` hands its command-line overrides to
# the nested make through these, and they outrank the test's `.env`.
MAKE_VARS = ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL")
STRIPPED = (*SSO_ENV, SIGNING_KEY_ENV, *LOCAL_ADMIN_ENV, *CLICKHOUSE_VARS, *MAKE_VARS)


def _doctor(checkout: Path, env_file: str | None) -> list[str]:
    shutil.copy(REPO_ROOT / "Makefile", checkout / "Makefile")
    if env_file is not None:
        (checkout / ".env").write_text(env_file)
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
    result = subprocess.run(["make", "-s", "doctor"], cwd=checkout, env=env, capture_output=True, text=True, check=True)
    return [line.strip() for line in result.stdout.splitlines()]


def _line(lines: list[str], prefix: str) -> str:
    matches = [line for line in lines if line.startswith(prefix)]
    assert len(matches) == 1, f"expected one `{prefix}` line, got {matches} in:\n" + "\n".join(lines)
    return matches[0]


def _assignments(names: tuple[str, ...]) -> str:
    return "".join(f"{name}=value-{i}\n" for i, name in enumerate(names))


NO_SSO = "SSO env: none set (no OAuth sign-in)"
ALL_SSO = "SSO env: all 4 OAuth client vars set (login enabled)"
NO_CLICKHOUSE = "CLICKHOUSE env: PARTIAL (0/3) — `make ch-bootstrap` will fail"
KEY_UNNEEDED = "SESSION_SIGNING_KEY: unset (fine with no sign-in method)"
KEY_SET = "SESSION_SIGNING_KEY: set"
KEY_MISSING = (
    "SESSION_SIGNING_KEY: MISSING or the dev default — sign-in is on, startup will fail (`openssl rand -hex 32`)"
)
REAL_KEY = f"{SIGNING_KEY_ENV}=a-real-random-secret\n"
DEV_KEY = f"{SIGNING_KEY_ENV}={DEV_SIGNING_KEY}\n"


@pytest.mark.parametrize(
    ("env_file", "sso", "key", "clickhouse"),
    [
        pytest.param(None, NO_SSO, KEY_UNNEEDED, NO_CLICKHOUSE, id="no-env-file"),
        pytest.param("", NO_SSO, KEY_UNNEEDED, NO_CLICKHOUSE, id="empty-env-file"),
        pytest.param(
            _assignments(SSO_ENV + CLICKHOUSE_VARS) + REAL_KEY,
            ALL_SSO,
            KEY_SET,
            "CLICKHOUSE env: all 3 set",
            id="all-set",
        ),
        pytest.param(
            _assignments(SSO_ENV[:2]) + REAL_KEY,
            "SSO env: PARTIAL (2/4) — startup will fail",
            KEY_SET,
            NO_CLICKHOUSE,
            id="some-set",
        ),
        pytest.param(REAL_KEY, NO_SSO, KEY_SET, NO_CLICKHOUSE, id="key-alone"),
        pytest.param(_assignments(SSO_ENV), ALL_SSO, KEY_MISSING, NO_CLICKHOUSE, id="oauth-without-key"),
        pytest.param(_assignments(LOCAL_ADMIN_ENV), NO_SSO, KEY_MISSING, NO_CLICKHOUSE, id="local-admin-without-key"),
        pytest.param(
            _assignments(LOCAL_ADMIN_ENV) + DEV_KEY, NO_SSO, KEY_MISSING, NO_CLICKHOUSE, id="local-admin-dev-key"
        ),
        pytest.param(
            _assignments(LOCAL_ADMIN_ENV) + REAL_KEY, NO_SSO, KEY_SET, NO_CLICKHOUSE, id="local-admin-real-key"
        ),
        pytest.param(
            "".join(f"{name}=x\n" for name in (*SSO_ENV, SIGNING_KEY_ENV)),
            ALL_SSO,
            KEY_SET,
            NO_CLICKHOUSE,
            id="one-character-values-count-as-set",
        ),
        pytest.param(
            _assignments(LOCAL_ADMIN_ENV) + f"{SIGNING_KEY_ENV}=   \t\n",
            NO_SSO,
            KEY_MISSING,
            NO_CLICKHOUSE,
            id="local-admin-whitespace-only-key",
        ),
        pytest.param(
            _assignments(LOCAL_ADMIN_ENV) + f"{SIGNING_KEY_ENV}={DEV_SIGNING_KEY}\r\n",
            NO_SSO,
            KEY_MISSING,
            NO_CLICKHOUSE,
            id="local-admin-crlf-dev-key",
        ),
        pytest.param(
            _assignments(LOCAL_ADMIN_ENV) + f"{SIGNING_KEY_ENV}={DEV_SIGNING_KEY} # placeholder\n",
            NO_SSO,
            KEY_MISSING,
            NO_CLICKHOUSE,
            id="local-admin-dev-key-with-inline-comment",
        ),
        pytest.param(
            _assignments(LOCAL_ADMIN_ENV) + REAL_KEY + DEV_KEY,
            NO_SSO,
            KEY_MISSING,
            NO_CLICKHOUSE,
            id="local-admin-real-key-then-dev-key",
        ),
        pytest.param(
            "DEFAULT_ADMIN_USERNAME=root@local\nDEFAULT_ADMIN_USERNAME=root@local\n",
            NO_SSO,
            KEY_UNNEEDED,
            NO_CLICKHOUSE,
            id="duplicated-username-without-password-is-not-sign-in",
        ),
    ],
)
def test_doctor_env_counts(tmp_path, env_file, sso, key, clickhouse):
    lines = _doctor(tmp_path, env_file)
    assert _line(lines, "SSO env:") == sso
    assert _line(lines, "SESSION_SIGNING_KEY:") == key
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
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
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
