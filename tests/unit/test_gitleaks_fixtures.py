"""Positive/negative control fixtures proving the gitleaks scan actually works.

Runs the real, pinned `gitleaks` binary (the same tool the pre-commit hook
and CI's secrets-scan.yml invoke) against two repository-safe fixtures under
tests/fixtures/secrets/:

- positive_control_credential.txt: a real-shaped (but AWS-documented,
  non-functional) example credential that gitleaks' default ruleset must
  detect and redact.
- negative_control_allowed_placeholder.txt: the same credential shape, at a
  path .gitleaks.toml's project allowlist explicitly covers.

Both fixture paths are ALSO in .gitleaks.toml's own allowlist (see that
file), so the routine full-repo scan (`make verify-secrets`, CI's
secrets-scan.yml) never perpetually flags them -- they're meant to stay
tracked in the repo forever. To still prove real detection happens, the
positive-control checks below deliberately scan with a bare, allowlist-free
config instead of the project's real one.

Both fixtures are safe to commit and to print: neither contains a live
secret, and the test only ever asserts on gitleaks' own (redacted) output,
never printing the fixture content itself.

Skipped when `gitleaks` isn't on PATH (e.g. an unprovisioned sandbox worker
per transit-app-gotchas). `.github/workflows/ci.yml`'s `test` job installs
the pinned gitleaks binary specifically so this module executes in CI
instead of silently skipping -- `.github/workflows/secrets-scan.yml` also
installs gitleaks, but only ever runs `gitleaks detect` directly and never
runs pytest, so it does not exercise these assertions. A properly
bootstrapped workstation/VPS clone (scripts/setup_git_hooks.sh) has
gitleaks installed too.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "secrets"
PROJECT_CONFIG = ROOT / ".gitleaks.toml"

# Read out of the fixture itself rather than duplicated as a literal here:
# a literal of this shape in this file would itself trip gitleaks' own
# aws-access-token rule on every commit that touches this test.
POSITIVE_SECRET = (FIXTURES_DIR / "positive_control_credential.txt").read_text().strip().rsplit("=", 1)[-1]

pytestmark = pytest.mark.skipif(
    shutil.which("gitleaks") is None,
    reason="gitleaks not installed; run scripts/setup_git_hooks.sh or see CLAUDE.md",
)


def _run_gitleaks(source: Path, config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "gitleaks",
            "detect",
            "--redact",
            "--no-banner",
            "--no-git",
            "--verbose",
            "--config",
            str(config),
            "--source",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _bare_config(tmp_path: Path) -> Path:
    """Default ruleset only, no project-specific allowlist -- proves
    detection is a property of gitleaks' rules, not an artifact of
    .gitleaks.toml happening to not exclude this path (yet)."""

    config = tmp_path / "bare.toml"
    config.write_text("[extend]\nuseDefault = true\n")
    return config


def test_positive_control_is_detected_and_redacted_by_default_rules():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_gitleaks(FIXTURES_DIR / "positive_control_credential.txt", _bare_config(Path(tmp)))

    assert result.returncode != 0, (
        "gitleaks did not flag the positive-control fixture under the default "
        f"ruleset; the hook would silently miss a real secret of this shape.\n"
        f"stdout={result.stdout}"
    )
    combined_output = result.stdout + result.stderr
    assert POSITIVE_SECRET not in combined_output, (
        "gitleaks printed the raw fixture secret instead of redacting it (--redact should have suppressed this)"
    )
    assert "REDACTED" in combined_output


def test_positive_control_is_excluded_from_the_routine_project_scan():
    """Confirms .gitleaks.toml's own allowlist covers this fixture, so
    `make verify-secrets`/CI's secrets-scan.yml don't perpetually fail on a
    fixture that's meant to stay in the repo forever."""

    result = _run_gitleaks(FIXTURES_DIR / "positive_control_credential.txt", PROJECT_CONFIG)
    assert result.returncode == 0, (
        "the positive-control fixture is flagged by the project's real "
        ".gitleaks.toml -- add it to the allowlist so routine scans stay "
        f"green.\nstdout={result.stdout}"
    )


def test_negative_control_is_suppressed_by_the_project_allowlist():
    result = _run_gitleaks(FIXTURES_DIR / "negative_control_allowed_placeholder.txt", PROJECT_CONFIG)

    assert result.returncode == 0, (
        "gitleaks flagged the negative-control fixture even though its path "
        "is allowlisted in .gitleaks.toml -- the allowlist mechanism used "
        "for .env.example and the BYOK test fixtures is broken.\n"
        f"stdout={result.stdout}"
    )


def test_negative_control_would_be_flagged_without_the_allowlist():
    """Sanity check that the clean result above comes from the allowlist,
    not from the fixture failing to match the rule at all (e.g. a typo in
    the credential shape)."""

    with tempfile.TemporaryDirectory() as tmp:
        result = _run_gitleaks(FIXTURES_DIR / "negative_control_allowed_placeholder.txt", _bare_config(Path(tmp)))

    assert result.returncode != 0, (
        "negative-control fixture wasn't detected even without the project "
        "allowlist -- it no longer matches gitleaks' aws-access-token rule, "
        "so the earlier clean result isn't evidence the allowlist works"
    )
