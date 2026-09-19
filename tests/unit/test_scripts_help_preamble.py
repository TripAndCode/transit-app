"""Every script in scripts/*.sh and scripts/dev/*.sh supports `-h`/`--help`:
it prints its own header comment and exits 0 before doing any real work
(reading required env vars, touching the network, a DB, or a subprocess).

Each script is invoked with a *minimal* environment (no ORACLE_HOST,
DATABASE_URL, OBJECT_STORE_*, etc.) so that a script whose --help check
doesn't come first -- and instead falls through into its normal
required-env-var checks -- fails this test with a nonzero exit code or the
wrong output, rather than silently passing because the ambient dev shell
happened to have those variables set.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPTS = sorted((ROOT / "scripts").glob("*.sh")) + sorted((ROOT / "scripts" / "dev").glob("*.sh"))

_MINIMAL_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin"}


def _run_help(script: Path, flag: str) -> subprocess.CompletedProcess[str]:
    interpreter = "sh" if script.read_text().splitlines()[0] == "#!/bin/sh" else "bash"
    return subprocess.run(
        [interpreter, str(script), flag],
        capture_output=True,
        text=True,
        timeout=10,
        env=_MINIMAL_ENV,
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_help_prints_header_and_exits_zero(script: Path) -> None:
    result = _run_help(script, "--help")
    assert result.returncode == 0, f"{script.name} --help: {result.stdout}{result.stderr}"
    assert result.stdout.strip(), f"{script.name} --help printed nothing"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_short_help_flag_also_works(script: Path) -> None:
    result = _run_help(script, "-h")
    assert result.returncode == 0, f"{script.name} -h: {result.stdout}{result.stderr}"
    assert result.stdout.strip(), f"{script.name} -h printed nothing"


def test_run_integration_tests_help_does_not_forward_to_pytest() -> None:
    script = ROOT / "scripts" / "run_integration_tests.sh"
    result = _run_help(script, "--help")
    assert result.returncode == 0
    # pytest's own --help output names its positional argument this way;
    # this script's usage line does not, so its presence would mean --help
    # fell through to `exec poetry run pytest --help` instead of being
    # intercepted.
    assert "file_or_dir" not in result.stdout
    assert "run_integration_tests.sh" in result.stdout
