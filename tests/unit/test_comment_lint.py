"""Tests for the comment-policy linter that narrows the `comments` review dimension."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "comment_lint.py"
SPEC = importlib.util.spec_from_file_location("comment_lint", SCRIPT)
assert SPEC and SPEC.loader
lint = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lint
SPEC.loader.exec_module(lint)


def rules(violations):
    """Reduce violations to the (line, rule) pairs a test cares about."""

    return [(v.line, v.rule) for v in violations]


def test_flags_python_comment_block_longer_than_threshold():
    lines = ["# a", "# b", "# c", "# d", "code = 1"]
    assert rules(lint.find_violations("x.py", lines, max_block=3)) == [(1, "long-block")]


def test_allows_python_comment_block_at_threshold():
    lines = ["# a", "# b", "# c", "code = 1"]
    assert lint.find_violations("x.py", lines, max_block=3) == []


def test_flags_slash_comment_block_in_tsx():
    lines = ["// a", "// b", "// c", "// d", "const x = 1;"]
    assert rules(lint.find_violations("x.tsx", lines, max_block=3)) == [(1, "long-block")]


def test_hash_is_not_a_comment_in_typescript():
    lines = ["# a", "# b", "# c", "# d", "const x = 1;"]
    assert lint.find_violations("x.ts", lines, max_block=3) == []


def test_slash_is_not_a_comment_in_python():
    lines = ["// a", "// b", "// c", "// d", "x = 1"]
    assert lint.find_violations("x.py", lines, max_block=3) == []


def test_ignores_tooling_pragmas_in_python():
    lines = ["# noqa: E501", "# type: ignore", "# fmt: off", "# pylint: skip", "x = 1"]
    assert lint.find_violations("x.py", lines, max_block=3) == []


def test_ignores_tooling_pragmas_in_typescript():
    lines = [
        "// eslint-disable-next-line",
        "// @ts-expect-error",
        "// prettier-ignore",
        "// eslint-disable",
        "const x = 1;",
    ]
    assert lint.find_violations("x.ts", lines, max_block=3) == []


def test_flags_python_banner():
    assert rules(lint.find_violations("x.py", ["# ==================", "x = 1"])) == [(1, "banner")]


def test_flags_typescript_banner():
    assert rules(lint.find_violations("x.ts", ["// ------------------", "const x = 1;"])) == [(1, "banner")]


def test_allows_short_rule_of_dashes():
    assert lint.find_violations("x.py", ["# --- ok ---", "x = 1"]) == []


def test_flags_reference_to_another_comment():
    found = lint.find_violations("x.py", ["# see that cross-check's comment", "x = 1"])
    assert rules(found) == [(1, "comment-xref")]


def test_flags_reference_to_another_doc():
    lines = ["// see MapLayerConfigBase.selectedFeatures's doc for why", "const x = 1;"]
    assert rules(lint.find_violations("x.tsx", lines)) == [(1, "comment-xref")]


def test_flags_positional_reference():
    found = lint.find_violations("x.py", ["# as noted above, this is fine", "x = 1"])
    assert rules(found) == [(1, "comment-xref")]


def test_allows_reference_to_a_symbol():
    assert lint.find_violations("x.py", ["# see ``process_delay_zip``", "x = 1"]) == []


def test_flags_line_number_reference():
    assert rules(lint.find_violations("x.py", ["# defined at line 412", "x = 1"])) == [(1, "line-ref")]


def test_allows_a_bare_number():
    assert lint.find_violations("x.py", ["# retries 3 times", "x = 1"]) == []


def test_reports_a_violation_the_diff_introduced():
    found = lint.find_violations("x.py", ["# ====", "x = 1"], only_lines={1})
    assert rules(found) == [(1, "banner")]


def test_ignores_a_violation_the_diff_did_not_touch():
    assert lint.find_violations("x.py", ["# ====", "x = 1"], only_lines={2}) == []


def test_ignores_a_pre_existing_block_edited_in_place():
    lines = ["# a", "# b", "# c", "# d", "x = 1"]
    assert lint.find_violations("x.py", lines, max_block=3, only_lines={3}) == []


def test_reports_a_block_the_diff_pushed_over_the_limit():
    """The block starts on an untouched line, but the added tail is what
    exceeded the limit, so scoping to added lines must still surface it."""

    lines = ["# a", "# b", "# c", "# d", "x = 1"]
    found = lint.find_violations("x.py", lines, max_block=3, only_lines={4})
    assert rules(found) == [(1, "long-block")]


def test_reports_untouched_comment_next_to_a_changed_line():
    lines = ["# explains the call", "do_work()", "unrelated()"]
    found = lint.stale_candidates("x.py", lines, changed={2}, radius=1)
    assert [c.line for c in found] == [1]


def test_ignores_a_comment_that_was_changed_itself():
    lines = ["# explains the call", "do_work()"]
    assert lint.stale_candidates("x.py", lines, changed={1, 2}, radius=1) == []


def test_ignores_a_comment_beyond_the_radius():
    lines = ["# far away", "a()", "b()", "do_work()"]
    assert lint.stale_candidates("x.py", lines, changed={4}, radius=1) == []


DIFF = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -3,0 +4,2 @@ def f():
+    # added one
+    # added two
@@ -20 +21 @@ def g():
-    old()
+    new()
diff --git a/src/b.tsx b/src/b.tsx
--- a/src/b.tsx
+++ b/src/b.tsx
@@ -0,0 +1 @@
+// brand new
"""


def test_maps_each_file_to_its_added_line_numbers():
    assert lint.parse_added_lines(DIFF) == {"src/a.py": {4, 5, 21}, "src/b.tsx": {1}}


def test_returns_nothing_for_an_empty_diff():
    assert lint.parse_added_lines("") == {}


def test_skips_a_deletion_only_hunk():
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -5 +4,0 @@\n-gone()\n"
    assert lint.parse_added_lines(diff) == {}


def test_blocks_when_a_gate_run_finds_something():
    assert lint.exit_code(count=3, gating=True, warn_only=False) == 2


def test_passes_when_a_gate_run_is_clean():
    assert lint.exit_code(count=0, gating=True, warn_only=False) == 0


def test_warn_only_never_blocks():
    assert lint.exit_code(count=3, gating=True, warn_only=True) == 0


def test_a_non_gating_run_never_blocks():
    assert lint.exit_code(count=3, gating=False, warn_only=False) == 0


def test_keeps_an_explicit_base():
    assert lint.resolve_base("v2.1", exists=lambda ref: True) == "v2.1"


def test_prefers_origin_head_when_auto():
    present = {"origin/HEAD", "master", "main"}
    assert lint.resolve_base("auto", exists=present.__contains__) == "origin/HEAD"


def test_falls_back_to_master_then_main():
    assert lint.resolve_base("auto", exists={"master", "main"}.__contains__) == "master"
    assert lint.resolve_base("auto", exists={"main"}.__contains__) == "main"


def test_returns_none_when_no_base_exists():
    assert lint.resolve_base("auto", exists=lambda ref: False) is None


def test_returns_none_when_an_explicit_base_is_missing():
    assert lint.resolve_base("nope", exists=lambda ref: False) is None


def git(repo: Path, *args: str) -> None:
    """Run Git in a temporary fixture repository."""

    subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Build a repo whose tip changes code beside an untouched comment."""

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    target = repo / "mod.py"
    target.write_text("# explains the call\nvalue = 1\n")
    git(repo, "add", "mod.py")
    git(repo, "commit", "-qm", "first")
    target.write_text("# explains the call\nvalue = 2\n")
    git(repo, "commit", "-qam", "second")
    return repo


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the script the way the review commands document."""

    return subprocess.run((sys.executable, str(SCRIPT), *args), capture_output=True, text=True)


def test_cli_stale_candidates_names_the_untouched_comment(repository):
    result = run_cli("--root", str(repository), "--stale-candidates", "HEAD~1")
    assert result.returncode == 0
    assert "mod.py:1" in result.stdout
    assert "stale-candidate" in result.stdout


def test_cli_stale_candidates_never_gates(repository):
    """The review dimension reads this mode's output; it must not fail the caller."""

    assert run_cli("--root", str(repository), "--stale-candidates", "HEAD~1").returncode == 0


def test_cli_diff_mode_gates_on_a_violation(repository):
    (repository / "mod.py").write_text("# explains the call\nvalue = 2\n# ==========\n")
    git(repository, "commit", "-qam", "banner")
    result = run_cli("--root", str(repository), "--diff", "HEAD~1")
    assert result.returncode == 2
    assert "banner" in result.stdout


def test_cli_warn_downgrades_a_gate_failure(repository):
    (repository / "mod.py").write_text("# explains the call\nvalue = 2\n# ==========\n")
    git(repository, "commit", "-qam", "banner")
    assert run_cli("--root", str(repository), "--diff", "HEAD~1", "--warn").returncode == 0


def test_cli_requires_a_mode():
    result = run_cli("--root", ".")
    assert result.returncode == 2
    assert "--baseline" in result.stderr


def test_cli_rejects_two_modes_at_once(repository):
    result = run_cli("--root", str(repository), "--diff", "HEAD~1", "--stale-candidates", "HEAD~1")
    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


def test_cli_skips_a_missing_base_instead_of_failing(repository):
    """A run that could not look is an anomaly, so it reports on stderr."""

    result = run_cli("--root", str(repository), "--stale-candidates", "no-such-ref")
    assert result.returncode == 0
    assert "no baseline ref" in result.stderr
    assert "no baseline ref" not in result.stdout


def test_cli_diff_mode_flags_a_block_the_diff_extended(repository):
    """Appending to an existing comment block is the common way to exceed the
    limit; the block's first line stays untouched, so anchoring on it alone
    would drop the finding."""

    (repository / "block.py").write_text("# one\n# two\n# three\n# four\n# five\n# six\nvalue = 1\n")
    git(repository, "add", "block.py")
    git(repository, "commit", "-qm", "six-line block, within the limit")
    assert run_cli("--root", str(repository), "--diff", "HEAD~1").returncode == 0

    (repository / "block.py").write_text("# one\n# two\n# three\n# four\n# five\n# six\n# seven\nvalue = 1\n")
    git(repository, "commit", "-qam", "one more line tips it over")
    result = run_cli("--root", str(repository), "--diff", "HEAD~1")
    assert result.returncode == 2
    assert "long-block" in result.stdout


def test_cli_baseline_scans_tracked_sources(repository):
    """`--baseline` sweeps the whole repository and never gates."""

    (repository / "banner.py").write_text("# ==========\nvalue = 1\n")
    git(repository, "add", "banner.py")
    git(repository, "commit", "-qm", "banner")
    result = run_cli("--root", str(repository), "--baseline")
    assert result.returncode == 0
    assert "banner.py:1" in result.stdout
    assert "banner" in result.stdout


def test_cli_baseline_degrades_outside_a_repository(tmp_path):
    """Every other mode degrades instead of crashing; this one now does too."""

    result = run_cli("--root", str(tmp_path), "--baseline")
    assert result.returncode == 0
    assert "skipped" in result.stderr
