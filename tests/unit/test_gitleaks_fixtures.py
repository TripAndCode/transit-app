"""Positive/negative control fixtures proving the gitleaks scan actually works.

Runs the real, pinned `gitleaks` binary (the same tool the pre-commit hook
and CI's secrets-scan.yml invoke) against two repository-safe fixtures under
tests/fixtures/secrets/:

- positive_control_credential.txt: a real-shaped (but AWS-documented,
  non-functional) example credential that must be detected and redacted.
- negative_control_allowed_placeholder.txt: the same credential shape, at a
  path .gitleaks.toml explicitly allowlists, which must NOT be flagged.

Both fixtures are safe to commit and to print: neither contains a live
secret, and the test only ever asserts on gitleaks' own (redacted) output,
never printing the fixture content itself.

Skipped when `gitleaks` isn't on PATH (e.g. an unprovisioned sandbox worker
per transit-app-gotchas) -- CI's secrets-scan.yml and a properly bootstrapped
workstation/VPS clone (scripts/setup_git_hooks.sh) both have it installed,
so this is the only environment where the check is silently absent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "secrets"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"
POSITIVE_SECRET = "AKIAIOSFODNN7EXAMPLE"

pytestmark = pytest.mark.skipif(
    shutil.which("gitleaks") is None,
    reason="gitleaks not installed; run scripts/setup_git_hooks.sh or see CLAUDE.md",
)


def _run_gitleaks(source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "gitleaks",
            "detect",
            "--redact",
            "--no-banner",
            "--no-git",
            "--verbose",
            "--config",
            str(GITLEAKS_CONFIG),
            "--source",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_positive_control_is_detected_and_redacted():
    result = _run_gitleaks(FIXTURES_DIR / "positive_control_credential.txt")

    assert result.returncode != 0, (
        "gitleaks did not flag the positive-control fixture; the hook would "
        f"silently miss a real secret of this shape.\nstdout={result.stdout}"
    )
    combined_output = result.stdout + result.stderr
    assert POSITIVE_SECRET not in combined_output, (
        "gitleaks printed the raw fixture secret instead of redacting it "
        "(--redact should have suppressed this)"
    )
    assert "REDACTED" in combined_output


def test_negative_control_placeholder_is_not_flagged():
    result = _run_gitleaks(FIXTURES_DIR / "negative_control_allowed_placeholder.txt")

    assert result.returncode == 0, (
        "gitleaks flagged the negative-control fixture even though its path "
        "is allowlisted in .gitleaks.toml -- the allowlist mechanism used "
        "for .env.example and the BYOK test fixtures is broken.\n"
        f"stdout={result.stdout}"
    )


def test_positive_control_alone_would_also_fail_the_allowlist_path():
    """Sanity check that the negative control's clean result above comes
    from the allowlist, not from the fixture failing to match the rule at
    all (e.g. a typo in the credential shape)."""

    with_config = _run_gitleaks(FIXTURES_DIR / "negative_control_allowed_placeholder.txt")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp_config_dir:
        empty_config = Path(tmp_config_dir) / "empty.toml"
        empty_config.write_text("[extend]\nuseDefault = true\n")
        result = subprocess.run(
            [
                "gitleaks",
                "detect",
                "--redact",
                "--no-banner",
                "--no-git",
                "--config",
                str(empty_config),
                "--source",
                str(FIXTURES_DIR / "negative_control_allowed_placeholder.txt"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    assert with_config.returncode == 0
    assert result.returncode != 0, (
        "negative-control fixture wasn't detected even without the project "
        "allowlist -- it no longer matches gitleaks' aws-access-token rule, "
        "so the earlier clean result isn't evidence the allowlist works"
    )
