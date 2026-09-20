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
    ("tests/api/test_api_copilot.py", "db003cf11ae8d1d50350acd672c246b50bb938e61a31b5c57acef0cce3f2d818"),
    ("tests/api/test_api_me_llm_key.py", "35ebf0d70b36f152cd0c628b7eccd3577b42c23a285bbf83b7f4f6fca687452a"),
    ("tests/db/test_user_llm_keys_db.py", "575465fabc7a0372f3533aea6d791615d1807a8150a592664a6d58d23821c8b2"),
    ("tests/unit/test_user_llm_keys.py", "b48124ed4d47a9c7662e5da513fa13299f22480dd3899ed19c31c0972253c81f"),
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
