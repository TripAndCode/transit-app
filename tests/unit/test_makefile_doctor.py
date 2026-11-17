"""`make doctor`'s `.env` checks, run for real against a scratch checkout.

The sign-in and ClickHouse lines count matching `.env` lines with `grep -c`,
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

from api.sso import DEV_SIGNING_KEY, SIGNING_KEY_ENV, SSO_ENV

REPO_ROOT = Path(__file__).resolve().parents[2]

LOCAL_ADMIN_VARS = ("DEFAULT_ADMIN_USERNAME", "DEFAULT_ADMIN_PASSWORD")
CLICKHOUSE_VARS = ("CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE")


def _doctor(checkout: Path, env_file: str | None) -> list[str]:
    shutil.copy(REPO_ROOT / "Makefile", checkout / "Makefile")
    if env_file is not None:
        (checkout / ".env").write_text(env_file)
    stripped = (*SSO_ENV, SIGNING_KEY_ENV, *LOCAL_ADMIN_VARS, *CLICKHOUSE_VARS)
    env = {k: v for k, v in os.environ.items() if k not in stripped}
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
        pytest.param(_assignments(LOCAL_ADMIN_VARS), NO_SSO, KEY_MISSING, NO_CLICKHOUSE, id="local-admin-without-key"),
        pytest.param(
            _assignments(LOCAL_ADMIN_VARS) + DEV_KEY, NO_SSO, KEY_MISSING, NO_CLICKHOUSE, id="local-admin-dev-key"
        ),
        pytest.param(
            _assignments(LOCAL_ADMIN_VARS) + REAL_KEY, NO_SSO, KEY_SET, NO_CLICKHOUSE, id="local-admin-real-key"
        ),
        pytest.param(
            "".join(f"{name}=x\n" for name in (*SSO_ENV, SIGNING_KEY_ENV)),
            ALL_SSO,
            KEY_SET,
            NO_CLICKHOUSE,
            id="one-character-values-count-as-set",
        ),
    ],
)
def test_doctor_env_counts(tmp_path, env_file, sso, key, clickhouse):
    lines = _doctor(tmp_path, env_file)
    assert _line(lines, "SSO env:") == sso
    assert _line(lines, "SESSION_SIGNING_KEY:") == key
    assert _line(lines, "CLICKHOUSE env:") == clickhouse
