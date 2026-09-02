"""Tests for the comment-policy linter that narrows the `comments` review dimension."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "comment_lint.py"
SPEC = importlib.util.spec_from_file_location("comment_lint", SCRIPT)
assert SPEC and SPEC.loader
lint = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lint
SPEC.loader.exec_module(lint)

exit_code = lint.exit_code
find_violations = lint.find_violations
parse_added_lines = lint.parse_added_lines
resolve_base = lint.resolve_base
stale_candidates = lint.stale_candidates


class TestLongBlock(unittest.TestCase):
    def test_flags_python_comment_block_longer_than_threshold(self):
        lines = ["# a", "# b", "# c", "# d", "code = 1"]
        found = find_violations("x.py", lines, max_block=3)
        self.assertEqual([(1, "long-block")], [(v.line, v.rule) for v in found])

    def test_allows_python_comment_block_at_threshold(self):
        lines = ["# a", "# b", "# c", "code = 1"]
        self.assertEqual([], find_violations("x.py", lines, max_block=3))


class TestTypeScript(unittest.TestCase):
    def test_flags_slash_comment_block_in_tsx(self):
        lines = ["// a", "// b", "// c", "// d", "const x = 1;"]
        found = find_violations("x.tsx", lines, max_block=3)
        self.assertEqual([(1, "long-block")], [(v.line, v.rule) for v in found])

    def test_hash_is_not_a_comment_in_typescript(self):
        lines = ["# a", "# b", "# c", "# d", "const x = 1;"]
        self.assertEqual([], find_violations("x.ts", lines, max_block=3))

    def test_slash_is_not_a_comment_in_python(self):
        lines = ["// a", "// b", "// c", "// d", "x = 1"]
        self.assertEqual([], find_violations("x.py", lines, max_block=3))


class TestPragmasAreNotProse(unittest.TestCase):
    def test_ignores_tooling_pragmas_in_python(self):
        lines = ["# noqa: E501", "# type: ignore", "# fmt: off", "# pylint: skip", "x = 1"]
        self.assertEqual([], find_violations("x.py", lines, max_block=3))

    def test_ignores_tooling_pragmas_in_typescript(self):
        lines = [
            "// eslint-disable-next-line",
            "// @ts-expect-error",
            "// prettier-ignore",
            "// eslint-disable",
            "const x = 1;",
        ]
        self.assertEqual([], find_violations("x.ts", lines, max_block=3))


class TestBanner(unittest.TestCase):
    def test_flags_python_banner(self):
        found = find_violations("x.py", ["# ==================", "x = 1"])
        self.assertEqual([(1, "banner")], [(v.line, v.rule) for v in found])

    def test_flags_typescript_banner(self):
        found = find_violations("x.ts", ["// ------------------", "const x = 1;"])
        self.assertEqual([(1, "banner")], [(v.line, v.rule) for v in found])

    def test_allows_short_rule_of_dashes(self):
        self.assertEqual([], find_violations("x.py", ["# --- ok ---", "x = 1"]))


class TestCrossReference(unittest.TestCase):
    def test_flags_reference_to_another_comment(self):
        found = find_violations("x.py", ["# see that cross-check's comment", "x = 1"])
        self.assertEqual([(1, "comment-xref")], [(v.line, v.rule) for v in found])

    def test_flags_reference_to_another_doc(self):
        lines = ["// see MapLayerConfigBase.selectedFeatures's doc for why", "const x = 1;"]
        found = find_violations("x.tsx", lines)
        self.assertEqual([(1, "comment-xref")], [(v.line, v.rule) for v in found])

    def test_flags_positional_reference(self):
        found = find_violations("x.py", ["# as noted above, this is fine", "x = 1"])
        self.assertEqual([(1, "comment-xref")], [(v.line, v.rule) for v in found])

    def test_allows_reference_to_a_symbol(self):
        self.assertEqual([], find_violations("x.py", ["# see ``process_delay_zip``", "x = 1"]))


class TestLineReference(unittest.TestCase):
    def test_flags_line_number_reference(self):
        found = find_violations("x.py", ["# defined at line 412", "x = 1"])
        self.assertEqual([(1, "line-ref")], [(v.line, v.rule) for v in found])

    def test_allows_a_bare_number(self):
        self.assertEqual([], find_violations("x.py", ["# retries 3 times", "x = 1"]))


class TestScopedToAddedLines(unittest.TestCase):
    def test_reports_a_violation_the_diff_introduced(self):
        lines = ["# ====", "x = 1"]
        found = find_violations("x.py", lines, only_lines={1})
        self.assertEqual([(1, "banner")], [(v.line, v.rule) for v in found])

    def test_ignores_a_violation_the_diff_did_not_touch(self):
        lines = ["# ====", "x = 1"]
        self.assertEqual([], find_violations("x.py", lines, only_lines={2}))

    def test_ignores_a_pre_existing_block_edited_in_place(self):
        lines = ["# a", "# b", "# c", "# d", "x = 1"]
        found = find_violations("x.py", lines, max_block=3, only_lines={3})
        self.assertEqual([], found)


class TestStaleCandidates(unittest.TestCase):
    def test_reports_untouched_comment_next_to_a_changed_line(self):
        lines = ["# explains the call", "do_work()", "unrelated()"]
        found = stale_candidates("x.py", lines, changed={2}, radius=1)
        self.assertEqual([1], [c.line for c in found])

    def test_ignores_a_comment_that_was_changed_itself(self):
        lines = ["# explains the call", "do_work()"]
        self.assertEqual([], stale_candidates("x.py", lines, changed={1, 2}, radius=1))

    def test_ignores_a_comment_beyond_the_radius(self):
        lines = ["# far away", "a()", "b()", "do_work()"]
        self.assertEqual([], stale_candidates("x.py", lines, changed={4}, radius=1))


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


class TestParseAddedLines(unittest.TestCase):
    def test_maps_each_file_to_its_added_line_numbers(self):
        self.assertEqual({"src/a.py": {4, 5, 21}, "src/b.tsx": {1}}, parse_added_lines(DIFF))

    def test_returns_nothing_for_an_empty_diff(self):
        self.assertEqual({}, parse_added_lines(""))

    def test_skips_a_deletion_only_hunk(self):
        diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -5 +4,0 @@\n-gone()\n"
        self.assertEqual({}, parse_added_lines(diff))


class TestExitCode(unittest.TestCase):
    def test_blocks_when_a_gate_run_finds_something(self):
        self.assertEqual(2, exit_code(count=3, gating=True, warn_only=False))

    def test_passes_when_a_gate_run_is_clean(self):
        self.assertEqual(0, exit_code(count=0, gating=True, warn_only=False))

    def test_warn_only_never_blocks(self):
        self.assertEqual(0, exit_code(count=3, gating=True, warn_only=True))

    def test_a_non_gating_run_never_blocks(self):
        self.assertEqual(0, exit_code(count=3, gating=False, warn_only=False))


class TestResolveBase(unittest.TestCase):
    def test_keeps_an_explicit_base(self):
        self.assertEqual("v2.1", resolve_base("v2.1", exists=lambda r: True))

    def test_prefers_origin_head_when_auto(self):
        present = {"origin/HEAD", "master", "main"}
        self.assertEqual("origin/HEAD", resolve_base("auto", exists=present.__contains__))

    def test_falls_back_to_master_then_main(self):
        self.assertEqual("master", resolve_base("auto", exists={"master", "main"}.__contains__))
        self.assertEqual("main", resolve_base("auto", exists={"main"}.__contains__))

    def test_returns_none_when_no_base_exists(self):
        self.assertIsNone(resolve_base("auto", exists=lambda r: False))

    def test_returns_none_when_an_explicit_base_is_missing(self):
        self.assertIsNone(resolve_base("nope", exists=lambda r: False))
