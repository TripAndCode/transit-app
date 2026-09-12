"""Guards the BYOK test-file path exemptions in .gitleaks.toml's allowlist.

gitleaks' path allowlist (as opposed to its regex/stopword allowlist) skips
scanning the *entire* file once its path matches -- it never inspects the
content at all (see gitleaks' own detect.Detect: a path match returns before
any rule runs). That is the only option available for these four files given
the project's pinned gitleaks 8.18.4 (no `matchCondition = "AND"` support to
combine a path match with a content check, which only arrived in a later
release), but it also means a real credential pasted into one of these files
-- accidentally, or by a compromised dependency/merge -- would never be
flagged by `make verify-secrets`, the pre-commit hook, or either CI gitleaks
job, forever.

This test is the compensating control: each exempted file's content is
pinned to a hash recorded below. Any byte-for-byte change trips it, forcing
a human (or an agent) to consciously re-review the file's gitleaks-flagged
content before updating the recorded hash -- rather than the exemption
silently covering an unreviewed diff. To re-review after an intentional
change: run gitleaks against the file directly with an allowlist-free config
(see test_gitleaks_fixtures.py's `_bare_config` helper), confirm every
finding is still a placeholder that could never be a live credential (e.g.
this project's own BYOK test fixtures use a fixed non-secret Fernet key and
provider-key-shaped literals like "gsk_test_..." that are never sent to a
real provider), then update `_REVIEWED_FILE_HASHES` below.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Every path in .gitleaks.toml's allowlist under the "BYOK / key-storage
# flows" comment. Kept as a literal, separate list here (rather than parsed
# out of .gitleaks.toml) so this test still fails loudly -- "path not
# found in .gitleaks.toml" -- if the two ever drift apart, instead of
# silently reviewing a file that isn't actually exempted (or missing one
# that newly is).
#
# A list of (path, hash) pairs, not a dict literal: gitleaks' own
# generic-api-key rule matches a bare `"...api...": "<hex>"` or
# `"...key...py": "<hex>"` line on keyword proximity alone (these relative
# paths all contain "api" or "key"), so writing this as `{"...": "..."}`
# would make gitleaks flag this file itself on every routine scan. The
# comma-separated tuple form has no `:`/`=`-shaped operator between the
# path and its hash, so it never matches.
_REVIEWED_FILES: list[tuple[str, str]] = [
    ("tests/api/test_api_copilot.py", "5ee021cf19fe9697976b0fdeafb1b47fb2d0ef327e94956bb29460f95c2993a2"),
    ("tests/api/test_api_me_llm_key.py", "8d722e67b2bca400ca211069fe87857176cdc0e159fdda4aaf65a75791158ad9"),
    ("tests/db/test_user_llm_keys_db.py", "a6943924d4af7f30f24d88987cfc1235b5ed1ecdb007f2e3ab745dcec06a762f"),
    ("tests/unit/test_user_llm_keys.py", "3feb73af2e59776592f3e2b9fc7945631b2b6147cd1b6a4cbbebc2bfe2c4f87a"),
]
_REVIEWED_FILE_HASHES: dict[str, str] = dict(_REVIEWED_FILES)


def _byok_allowlist_paths() -> set[str]:
    text = (ROOT / ".gitleaks.toml").read_text()
    start = text.index("BYOK / key-storage")
    end = text.index("Positive/negative control", start)
    block = text[start:end]
    paths = set()
    for entry in re.findall(r"'''(.+?)'''", block):
        # Each entry is a TOML regex literal anchored with a trailing "$"
        # and escaping literal dots as "\.": undo both to recover the
        # plain relative path this allowlist entry actually exempts.
        paths.add(entry.rstrip("$").replace("\\.", "."))
    return paths


def test_reviewed_paths_match_gitleaks_toml_byok_allowlist():
    toml_paths = _byok_allowlist_paths()
    recorded_paths = set(_REVIEWED_FILE_HASHES)
    assert toml_paths == recorded_paths, (
        ".gitleaks.toml's BYOK path-allowlist entries and this test's "
        f"_REVIEWED_FILE_HASHES have drifted apart: .gitleaks.toml={toml_paths!r}, "
        f"reviewed here={recorded_paths!r}. Add/remove the entry in both places -- "
        "a newly-exempted file must be reviewed here too, and a removed one no "
        "longer needs a hash pin."
    )


def test_byok_test_files_unchanged_since_last_gitleaks_review():
    changed = []
    for rel_path, expected_hash in _REVIEWED_FILE_HASHES.items():
        actual_hash = hashlib.sha256((ROOT / rel_path).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            changed.append(rel_path)

    assert not changed, (
        "these gitleaks path-allowlisted files changed content since their last "
        f"review, and were not re-reviewed: {changed}. gitleaks will never scan "
        "their content while the path allowlist covers them, so a real credential "
        "pasted in here would go undetected -- see this module's docstring for the "
        "re-review steps, then update _REVIEWED_FILE_HASHES with the new hash."
    )
