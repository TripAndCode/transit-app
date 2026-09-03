"""Tests for the build-time extension gate in db/assert_extensions.sh.

The gate runs inside the image build, where `dpkg` supplies version
comparison. These tests stub `dpkg` on PATH so the script's own control flow
is pinned everywhere the suite runs. What they pin, specifically: an inverted
comparison, a dropped `exit 1` on any failure branch, and a Dockerfile that
stops running the gate or lets it fail without failing the build.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "db" / "assert_extensions.sh"

# Mirrors `dpkg --compare-versions a ge b`: exit 0 when the relation holds.
DPKG_STUB = """#!/bin/sh
[ "$1" = "--compare-versions" ] || exit 2
python3 - "$2" "$3" "$4" <<'PY'
import sys
def parts(v):
    return [int(x) for x in v.split(".")]
left, relation, right = sys.argv[1], sys.argv[2], sys.argv[3]
assert relation == "ge", relation
sys.exit(0 if parts(left) >= parts(right) else 1)
PY
"""


@pytest.fixture
def extension_dir(tmp_path: Path) -> Path:
    """An extension directory holding the three control files a build needs."""

    ext = tmp_path / "extension"
    ext.mkdir()
    write_control(ext, "postgis", "3.6.4")
    write_control(ext, "vector", "0.8.6")
    write_control(ext, "pg_trgm", "1.6")
    return ext


@pytest.fixture
def stub_path(tmp_path: Path) -> Path:
    """A PATH entry providing a deterministic `dpkg`."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "dpkg"
    stub.write_text(DPKG_STUB)
    stub.chmod(0o755)
    return bin_dir


def write_control(ext: Path, name: str, version: str) -> None:
    """Write the one line the script reads out of a control file."""

    (ext / f"{name}.control").write_text(f"default_version = '{version}'\ncomment = 'x'\n")


def run(ext: Path, stub_path: Path, *specs: str) -> subprocess.CompletedProcess[str]:
    """Invoke the gate the way the Dockerfile does."""

    env = {**os.environ, "PATH": f"{stub_path}:{os.environ['PATH']}"}
    return subprocess.run(
        ["sh", str(SCRIPT), str(ext), *specs],
        capture_output=True,
        text=True,
        env=env,
    )


DEFAULT_SPECS = ("postgis=3.2", "vector=0.8", "pg_trgm")


def test_passes_on_a_legitimate_installation(extension_dir, stub_path):
    result = run(extension_dir, stub_path, *DEFAULT_SPECS)
    assert result.returncode == 0, result.stderr
    assert "postgis 3.6.4" in result.stdout
    assert "vector 0.8.6" in result.stdout
    assert "pg_trgm present" in result.stdout


def test_fails_when_an_extension_is_absent(extension_dir, stub_path):
    (extension_dir / "pg_trgm.control").unlink()
    result = run(extension_dir, stub_path, *DEFAULT_SPECS)
    assert result.returncode == 1
    assert "missing extension: pg_trgm" in result.stderr


@pytest.mark.parametrize("version", ["3.1.9", "3.1", "2.5.0"])
def test_fails_below_the_floor(extension_dir, stub_path, version):
    write_control(extension_dir, "postgis", version)
    result = run(extension_dir, stub_path, *DEFAULT_SPECS)
    assert result.returncode == 1
    assert f"postgis {version} is below the required 3.2" in result.stderr


@pytest.mark.parametrize("version", ["3.2", "3.2.0", "3.2.1", "4.0"])
def test_passes_at_or_above_the_floor(extension_dir, stub_path, version):
    write_control(extension_dir, "postgis", version)
    assert run(extension_dir, stub_path, *DEFAULT_SPECS).returncode == 0


def test_fails_when_a_version_cannot_be_read(extension_dir, stub_path):
    (extension_dir / "vector.control").write_text("comment = 'no version here'\n")
    result = run(extension_dir, stub_path, *DEFAULT_SPECS)
    assert result.returncode == 1
    assert "cannot read a version for vector" in result.stderr


def test_rejects_being_called_without_specs(extension_dir, stub_path):
    result = run(extension_dir, stub_path)
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_the_dockerfile_runs_the_gate_and_lets_it_fail_the_build():
    """The gate is only real if the image runs it and heeds its exit status.

    Matched as a whole line, so a trailing `|| true` — which would keep every
    substring below present while making the build succeed regardless — fails
    this test rather than passing it.
    """

    dockerfile = (ROOT / "db" / "Dockerfile").read_text()
    assert "COPY assert_extensions.sh /usr/local/bin/assert-extensions" in dockerfile

    logical_lines = re.sub(r"\\\n\s*", " ", dockerfile)
    invocation = re.compile(
        r"^RUN\s+/usr/local/bin/assert-extensions"
        r"\s+/usr/share/postgresql/14/extension"
        r"\s+postgis=3\.2\s+vector=0\.8\s+pg_trgm\s*$",
        re.MULTILINE,
    )
    assert invocation.search(logical_lines), (
        "the Dockerfile must run the gate as its own command, with nothing appended that could swallow a non-zero exit"
    )

    # A SHELL directive rewrites how every later RUN is invoked, so one ending
    # in `|| true` makes the gate inert while leaving its own line untouched.
    # This image has no reason to override the default shell at all.
    assert not re.search(r"^\s*SHELL\b", dockerfile, re.MULTILINE | re.IGNORECASE), (
        "db/Dockerfile must not override SHELL: it changes how every RUN reports its exit status, including the gate's"
    )


def test_rejects_a_spec_with_an_empty_minimum(extension_dir, stub_path):
    """`name=` must not degrade into a presence-only check that always passes."""

    result = run(extension_dir, stub_path, "postgis=")
    assert result.returncode == 2
    assert "no minimum version given for postgis" in result.stderr
