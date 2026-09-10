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

    new_lines, changes = reconcile.merge_duplicate_level2_sections(lines_of(text))
    result = "".join(new_lines)

    assert len(changes) == 1
    assert "Restored Backlog Entries" in changes[0]
    assert "line 5" in changes[0]
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

    new_lines, changes = reconcile.merge_duplicate_level2_sections(lines_of(text))

    assert "".join(new_lines) == text
    assert changes == []


def test_merge_duplicate_level2_sections_handles_no_headings():
    text = "just some text\nwith no headings at all\n"

    new_lines, changes = reconcile.merge_duplicate_level2_sections(lines_of(text))

    assert "".join(new_lines) == text
    assert changes == []


def test_merge_duplicate_level2_sections_leaves_differently_worded_headings_alone():
    text = "## Section A\n\nbody\n\n## Section A (continued)\n\nother body\n"

    new_lines, changes = reconcile.merge_duplicate_level2_sections(lines_of(text))

    assert "".join(new_lines) == text
    assert changes == []


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
