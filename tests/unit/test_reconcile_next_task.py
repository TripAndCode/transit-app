"""Tests for reconciling NEXT_TASK.md against merged PRs and duplicate sections."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "reconcile_next_task.py"
SPEC = importlib.util.spec_from_file_location("reconcile_next_task", SCRIPT)
assert SPEC and SPEC.loader
reconcile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reconcile
SPEC.loader.exec_module(reconcile)


def lines_of(text: str) -> list[str]:
    """Split like `Path.read_text().splitlines(keepends=True)` would."""

    return text.splitlines(keepends=True)


# --- parse_items / has_terminal_marker -------------------------------------


def test_parse_items_finds_headers_and_ignores_indented_continuation_lines():
    text = (
        "# Refactor backlog\n"
        "\n"
        "1. **First item.** Some detail.\n"
        "   continuation line, indented, not a new item\n"
        "2. **Second item.**\n"
    )
    items = reconcile.parse_items(lines_of(text))

    assert [item.number for item in items] == [1, 2]
    assert items[0].line_index == 2
    assert items[1].line_index == 4


def test_parse_items_ignores_item_like_lines_inside_a_fenced_code_block():
    # Mirrors find_headings's own fence-awareness test: a Status log entry
    # that quotes this file's own "N. **..." item-header syntax inside a
    # fenced example must not be misread as a real backlog item by the
    # parser reconcile_item_statuses relies on.
    text = (
        "1. **Real item.**\n"
        "\n"
        "```\n"
        "99. **Not a real item, just a quoted example.**\n"
        "```\n"
        "\n"
        "2. **Another real item.**\n"
    )
    items = reconcile.parse_items(lines_of(text))

    assert [item.number for item in items] == [1, 2]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("108. **Make X authoritative.**\n", False),
        ("107. **DONE (PR #381, merged 2026-09-10) — Replace X.**\n", True),
        ("100. **MOOT — premise no longer holds.**\n", True),
        ("3. **DO NOT START — blocked on Y.**\n", True),
        ("9. **Not bold at all\n", False),
    ],
)
def test_has_terminal_marker(header: str, expected: bool):
    assert reconcile.has_terminal_marker(header) is expected


# --- parse_merged_prs_payload ------------------------------------------------


def test_parse_merged_prs_payload_keeps_only_item_branches_and_highest_pr():
    payload = [
        {"number": 381, "headRefName": "vps-loop/item-107", "mergedAt": "2026-09-10T14:35:00Z"},
        {"number": 12, "headRefName": "some-other-branch", "mergedAt": "2026-01-01T00:00:00Z"},
        # A second, higher-numbered PR reusing the same item branch (defensive case;
        # should not realistically happen once a branch is merged, but must not crash).
        {"number": 390, "headRefName": "vps-loop/item-107", "mergedAt": "2026-09-11T00:00:00Z"},
        {"number": 5, "headRefName": "vps-loop/item-3", "mergedAt": None},
    ]

    merged = reconcile.parse_merged_prs_payload(payload)

    assert merged[107] == reconcile.MergedPR(pr_number=390, merged_date="2026-09-11")
    assert merged[3] == reconcile.MergedPR(pr_number=5, merged_date="unknown-date")
    assert set(merged) == {107, 3}


def test_parse_merged_prs_payload_raises_reconcile_error_on_missing_number():
    # `gh pr list` should always include "number" when requested, but if a future
    # response ever omits it, this must surface as the script's own clean
    # ReconcileError (caught by main()'s error path), never a raw KeyError.
    payload = [{"headRefName": "vps-loop/item-108", "mergedAt": "2026-09-11T00:00:00Z"}]

    with pytest.raises(reconcile.ReconcileError):
        reconcile.parse_merged_prs_payload(payload)


def test_parse_merged_prs_payload_raises_reconcile_error_on_non_numeric_number():
    # A malformed `gh` response with a non-numeric "number" must surface as the
    # script's own clean ReconcileError (caught by main()'s error path), never
    # a raw ValueError/TypeError.
    payload = [{"number": "not-a-number", "headRefName": "vps-loop/item-108", "mergedAt": "2026-09-11T00:00:00Z"}]

    with pytest.raises(reconcile.ReconcileError):
        reconcile.parse_merged_prs_payload(payload)


# --- reconcile_item_statuses --------------------------------------------------


def test_reconcile_item_statuses_marks_merged_item_done():
    text = "108. **Make X authoritative.** More detail here.\n109. **Some other item.**\n"
    merged = {108: reconcile.MergedPR(pr_number=400, merged_date="2026-09-11")}

    new_lines, changes, warnings = reconcile.reconcile_item_statuses(lines_of(text), merged)

    assert "".join(new_lines) == (
        "108. **DONE (PR #400, merged 2026-09-11) — Make X authoritative.** More detail here.\n"
        "109. **Some other item.**\n"
    )
    assert changes == ["item 108: marked DONE (PR #400, merged 2026-09-11)"]
    assert warnings == []


def test_reconcile_item_statuses_leaves_already_marked_items_untouched():
    text = "107. **DONE (PR #381, merged 2026-09-10) — Replace X.**\n"
    merged = {107: reconcile.MergedPR(pr_number=381, merged_date="2026-09-10")}

    new_lines, changes, warnings = reconcile.reconcile_item_statuses(lines_of(text), merged)

    assert "".join(new_lines) == text
    assert changes == []
    assert warnings == []


def test_reconcile_item_statuses_leaves_moot_items_untouched_even_if_a_branch_merged():
    # A MOOT item's own branch should never realistically show up as merged, but if
    # it does (e.g. a stale/reused branch name), an existing terminal marker of any
    # kind wins — never overwritten with a DONE prefix.
    text = "100. **MOOT — premise no longer holds.**\n"
    merged = {100: reconcile.MergedPR(pr_number=999, merged_date="2026-09-11")}

    new_lines, changes, _warnings = reconcile.reconcile_item_statuses(lines_of(text), merged)

    assert "".join(new_lines) == text
    assert changes == []


def test_reconcile_item_statuses_skips_duplicated_item_numbers_without_modifying_either_copy():
    # Two same-numbered item lines (e.g. one left over in the main backlog, one
    # in a restored-entries section) must never be resolved by the dict
    # comprehension's arbitrary last-wins pick: applying the DONE marker to
    # whichever copy happens to survive that pick would leave its sibling
    # stale and contradict the "never guess which copy is authoritative"
    # invariant this module documents and enforces elsewhere.
    text = "108. **First copy, still open.**\n108. **Second copy, still open.**\n"
    merged = {108: reconcile.MergedPR(pr_number=400, merged_date="2026-09-11")}

    new_lines, changes, warnings = reconcile.reconcile_item_statuses(lines_of(text), merged)

    assert "".join(new_lines) == text
    assert changes == []
    assert len(warnings) == 1
    assert "item-108" in warnings[0]
    assert "appears more than once" in warnings[0]


def test_reconcile_item_statuses_warns_on_merged_branch_with_no_matching_item():
    text = "1. **Only item.**\n"
    merged = {42: reconcile.MergedPR(pr_number=7, merged_date="2026-09-11")}

    new_lines, changes, warnings = reconcile.reconcile_item_statuses(lines_of(text), merged)

    assert "".join(new_lines) == text
    assert changes == []
    assert warnings == [
        "vps-loop/item-42 has merged PR #7, but no numbered backlog item 42 was found in the file"
    ]


# --- merge_duplicate_level2_sections ------------------------------------------


def test_merge_duplicate_level2_sections_combines_bodies_and_drops_second_heading():
    text = (
        "# Refactor backlog\n"
        "\n"
        "1. **First.**\n"
        "\n"
        "## Restored Backlog Entries\n"
        "\n"
        "87. **Old restored item.**\n"
        "\n"
        "# Status log\n"
        "\n"
        "- entry\n"
        "\n"
        "## Restored Backlog Entries\n"
        "\n"
        "108. **New restored item.**\n"
    )

    new_lines, changes, warnings = reconcile.merge_duplicate_level2_sections(lines_of(text))
    result = "".join(new_lines)

    assert len(changes) == 1
    assert "Restored Backlog Entries" in changes[0]
    assert "line 5" in changes[0]
    assert warnings == []
    # Only one heading survives.
    assert result.count("## Restored Backlog Entries") == 1
    # Both items now live under the single, first heading.
    assert "87. **Old restored item.**" in result
    assert "108. **New restored item.**" in result
    restored_index = result.index("## Restored Backlog Entries")
    assert restored_index < result.index("87. **Old restored item.**")
    assert result.index("87. **Old restored item.**") < result.index("108. **New restored item.**")
    # The merged content lands inside the (single) Restored Backlog Entries
    # section, before the unrelated Status log section that originally sat
    # between the two duplicate headings.
    assert result.index("108. **New restored item.**") < result.index("# Status log")
    # The unrelated Status log section and its entry are preserved untouched.
    assert "- entry\n" in result


def test_merge_duplicate_level2_sections_is_a_noop_when_headings_are_unique():
    text = "# Top\n\n## Section A\n\nbody\n\n## Section B\n\nother body\n"

    new_lines, changes, warnings = reconcile.merge_duplicate_level2_sections(lines_of(text))

    assert "".join(new_lines) == text
    assert changes == []
    assert warnings == []


def test_merge_duplicate_level2_sections_handles_no_headings():
    text = "just some text\nwith no headings at all\n"

    new_lines, changes, warnings = reconcile.merge_duplicate_level2_sections(lines_of(text))

    assert "".join(new_lines) == text
    assert changes == []
    assert warnings == []


def test_merge_duplicate_level2_sections_leaves_differently_worded_headings_alone():
    text = "## Section A\n\nbody\n\n## Section A (continued)\n\nother body\n"

    new_lines, changes, warnings = reconcile.merge_duplicate_level2_sections(lines_of(text))

    assert "".join(new_lines) == text
    assert changes == []
    assert warnings == []


def test_merge_duplicate_level2_sections_warns_on_same_numbered_item_across_sections():
    # Unlike the distinct-numbers dedup test above (87 vs. 108), both duplicate
    # sections here carry an item *87* — the merge must not silently keep only
    # one of the two copies without flagging the fork.
    text = (
        "# Refactor backlog\n"
        "\n"
        "## Restored Backlog Entries\n"
        "\n"
        "87. **Old restored item, still open.**\n"
        "\n"
        "# Status log\n"
        "\n"
        "- entry\n"
        "\n"
        "## Restored Backlog Entries\n"
        "\n"
        "87. **DONE (PR #200, merged 2026-09-01) — Old restored item, still open.**\n"
    )

    new_lines, changes, warnings = reconcile.merge_duplicate_level2_sections(lines_of(text))
    result = "".join(new_lines)

    assert len(changes) == 1
    # Both copies of item 87 survive the merge untouched...
    assert result.count("87. **") == 2
    # ...but the fork is surfaced instead of silently dropping one copy.
    assert len(warnings) == 1
    assert "item 87" in warnings[0]


def test_find_duplicate_item_numbers_returns_sorted_numbers_seen_more_than_once():
    text = "1. **A.**\n2. **B.**\n1. **A again.**\n3. **C.**\n3. **C again.**\n"

    assert reconcile.find_duplicate_item_numbers(lines_of(text)) == [1, 3]


def test_find_duplicate_item_numbers_empty_when_all_numbers_unique():
    text = "1. **A.**\n2. **B.**\n"

    assert reconcile.find_duplicate_item_numbers(lines_of(text)) == []


def test_duplicate_item_number_warnings_covers_two_same_numbered_items_with_no_heading_at_all():
    # No `## ` heading anywhere in this text, let alone a duplicate one — the two
    # same-numbered items are already sitting side by side in the same section,
    # e.g. left over from a manual restore or a prior tick. This must still be
    # flagged; it's not conditional on any heading-merge byproduct.
    text = "108. **First copy.**\n108. **Second copy.**\n"

    warnings = reconcile.duplicate_item_number_warnings(lines_of(text))

    assert len(warnings) == 1
    assert "backlog item 108 appears more than once" in warnings[0]


# --- find_headings / fenced code blocks ---------------------------------------


def test_find_headings_ignores_heading_like_lines_inside_a_fenced_code_block():
    text = (
        "# Real heading\n"
        "\n"
        "```\n"
        "# not a real heading, just quoted output\n"
        "## also not real\n"
        "```\n"
        "\n"
        "## Another real heading\n"
    )

    headings = reconcile.find_headings(lines_of(text))

    assert [title for _index, _level, title in headings] == ["Real heading", "Another real heading"]


def test_find_headings_handles_tilde_fences_too():
    text = "~~~\n# inside a tilde fence\n~~~\n## Real heading\n"

    headings = reconcile.find_headings(lines_of(text))

    assert [title for _index, _level, title in headings] == ["Real heading"]


def test_find_headings_ignores_heading_like_lines_inside_a_four_space_indented_fence():
    # NEXT_TASK.md's fenced blocks live inside numbered-list-item continuation
    # text and are conventionally indented 4 spaces — more than the 3-space
    # cap a strict top-level CommonMark parser would still treat as a fence.
    text = (
        "# Real heading\n"
        "\n"
        "1. **Item.**\n"
        "\n"
        "    ```\n"
        "    # not a real heading, just quoted output\n"
        "    ```\n"
        "\n"
        "## Another real heading\n"
    )

    headings = reconcile.find_headings(lines_of(text))

    assert [title for _index, _level, title in headings] == ["Real heading", "Another real heading"]


def test_merge_duplicate_level2_sections_ignores_heading_like_lines_in_status_log_code_block():
    # A Status log entry that happens to quote a fenced snippet containing a
    # line starting with "#" at column 0 must not be misread as a section
    # boundary and corrupt where a real duplicate section's body starts/ends.
    text = (
        "# Refactor backlog\n"
        "\n"
        "## Restored Backlog Entries\n"
        "\n"
        "87. **Old restored item.**\n"
        "\n"
        "# Status log\n"
        "\n"
        "- entry with a quoted snippet:\n"
        "  ```\n"
        "  # looks like a heading but is inside a fence\n"
        "  ```\n"
        "\n"
        "## Restored Backlog Entries\n"
        "\n"
        "108. **New restored item.**\n"
    )

    new_lines, changes, warnings = reconcile.merge_duplicate_level2_sections(lines_of(text))
    result = "".join(new_lines)

    assert len(changes) == 1
    assert warnings == []
    assert result.count("## Restored Backlog Entries") == 1
    assert "87. **Old restored item.**" in result
    assert "108. **New restored item.**" in result
    assert "# looks like a heading but is inside a fence" in result


# --- main() end-to-end ---------------------------------------------------------


def test_main_dry_run_reports_changes_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    target = tmp_path / "NEXT_TASK.md"
    original = "108. **Make X authoritative.**\n"
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        reconcile,
        "load_merged_item_prs",
        lambda _repo: {108: reconcile.MergedPR(pr_number=400, merged_date="2026-09-11")},
    )

    # main() reads argv via argparse; invoke it directly with a patched sys.argv.
    monkeypatch.setattr(sys, "argv", ["reconcile_next_task.py", "--repo", str(tmp_path), "--file", str(target)])
    exit_code = reconcile.main()

    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == original  # unchanged: dry run
    out = capsys.readouterr().out
    assert "RECONCILE:" in out
    assert "Dry run only" in out


def test_main_apply_writes_the_reconciled_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "NEXT_TASK.md"
    target.write_text("108. **Make X authoritative.**\n", encoding="utf-8")
    monkeypatch.setattr(
        reconcile,
        "load_merged_item_prs",
        lambda _repo: {108: reconcile.MergedPR(pr_number=400, merged_date="2026-09-11")},
    )
    monkeypatch.setattr(
        sys, "argv", ["reconcile_next_task.py", "--repo", str(tmp_path), "--file", str(target), "--apply"]
    )

    exit_code = reconcile.main()

    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == (
        "108. **DONE (PR #400, merged 2026-09-11) — Make X authoritative.**\n"
    )


def test_main_reports_error_for_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    missing = tmp_path / "NEXT_TASK.md"
    monkeypatch.setattr(sys, "argv", ["reconcile_next_task.py", "--repo", str(tmp_path), "--file", str(missing)])

    exit_code = reconcile.main()

    assert exit_code == 2
    assert "does not exist" in capsys.readouterr().err


def test_main_warns_on_duplicate_item_numbers_with_no_duplicate_heading_involved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    # Reproduces the original failure mode through a different door: two items
    # already share a number in the same section (e.g. left over from a manual
    # restore or a prior tick) with no duplicate `## ` heading anywhere in the
    # file, so `merge_duplicate_level2_sections` never runs its own duplicate-
    # number check. `main` must still catch this on every run, not only as a
    # byproduct of a heading merge finding something to merge.
    target = tmp_path / "NEXT_TASK.md"
    target.write_text(
        "108. **First copy.**\n109. **Some other item.**\n108. **Second copy, same number.**\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reconcile, "load_merged_item_prs", lambda _repo: {})
    monkeypatch.setattr(sys, "argv", ["reconcile_next_task.py", "--repo", str(tmp_path), "--file", str(target)])

    exit_code = reconcile.main()

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "backlog item 108 appears more than once" in err
