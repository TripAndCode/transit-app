"""Guards the scope of .gitleaks.toml's path allowlist.

gitleaks' path allowlist (as opposed to its regex/stopword allowlist) skips
scanning the *entire* file once its path matches -- it never inspects the
content at all (see gitleaks' own detect.Detect: a path match returns before
any rule runs). That is the only option available for these files given the
project's pinned gitleaks 8.18.4 (no `matchCondition = "AND"` support to
combine a path match with a content check, which only arrived in a later
release), but it also means a real credential pasted into one of these files
-- accidentally, or by a compromised dependency/merge -- would never be
flagged by `make verify-secrets`, the pre-commit hook, or either CI gitleaks
job, forever.

This test is the compensating control: it pins the full set of exempted
paths to a literal below, so a new entry can't widen the exemption without a
human (or an agent) consciously reviewing it here too. `test_gitleaks_fixtures.py`
covers the two control fixtures' content directly, with an allowlist-free
config.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_EXPECTED_PATHS = {
    r"""\.env\.example$""",
    r"""tests/fixtures/secrets/positive_control_credential\.txt$""",
    r"""tests/fixtures/secrets/negative_control_allowed_placeholder\.txt$""",
}


def test_allowlist_paths_match_the_reviewed_set():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    actual_paths = set(config["allowlist"]["paths"])
    assert actual_paths == _EXPECTED_PATHS, (
        ".gitleaks.toml's allowlist paths have drifted from this test's reviewed "
        f"set: toml={actual_paths!r}, expected={_EXPECTED_PATHS!r}. A new entry "
        "widens what gitleaks never scans the content of -- review it, then add "
        "it to _EXPECTED_PATHS here too."
    )
