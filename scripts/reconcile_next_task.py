#!/usr/bin/env python3
"""Reconcile ``NEXT_TASK.md`` against actual GitHub PR state and structure.

``NEXT_TASK.md`` is untracked (`/vps-loop-run`'s own input/status log — see
CLAUDE.md's Autonomous VPS loop section) and gets edited by many independent
ticks, occasional coordinator-direct fixes, and the occasional human. Nothing
enforces that an item's own bold status header actually reflects whether its
branch already merged: a tick can ship a PR and die before rewriting the
item's text (Step 6.11 never runs), a human can merge a PR by hand outside
the loop entirely, or a resumed branch can ship without ever revisiting the
original item text. Any of these leaves the backlog claiming still-open work
that has, in fact, already landed on `main`.

This script performs two independent, individually-safe repairs so that
drift like the above self-heals instead of silently accumulating:

1. **Reconcile merged PRs.** For every backlog item numbered ``N. **...**``
   whose branch ``vps-loop/item-N`` has a merged PR on GitHub, but whose own
   bold header doesn't already start with a terminal status word (``DONE``,
   ``MOOT``, or ``DO NOT START``), prefix that header with a normalized
   ``DONE (PR #<number>, merged <date>) — `` marker — the exact convention
   already used by hand-written entries elsewhere in the file. This never
   rewrites anything beyond that prefix, so an item that already carries its
   own DONE/MOOT/DO NOT START language (including a nuanced "shipped as part
   of a different PR" note) is left untouched.
2. **De-duplicate level-2 sections.** If the same ``## `` heading text (e.g.
   "Restored Backlog Entries") appears more than once — the shape a second,
   unaware restoration would produce — merge every later occurrence's body
   into the first occurrence and drop the now-redundant heading line, so the
   backlog can never silently fork into two same-named sections.

Like `cleanup_git_state.py`, planning is the default; pass `--apply` to
actually write the result.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Reuse cleanup_git_state.py's already-reviewed subprocess/error-handling
# helpers instead of duplicating them. Loaded by file path since `scripts/`
# has no `__init__.py` and isn't guaranteed to be on `sys.path` (same
# approach as `daily_git_hygiene.py`).
_SCRIPT_DIR = Path(__file__).resolve().parent
_CLEANUP_SPEC = importlib.util.spec_from_file_location("cleanup_git_state", _SCRIPT_DIR / "cleanup_git_state.py")
assert _CLEANUP_SPEC and _CLEANUP_SPEC.loader
cleanup_git_state = importlib.util.module_from_spec(_CLEANUP_SPEC)
sys.modules[_CLEANUP_SPEC.name] = cleanup_git_state
_CLEANUP_SPEC.loader.exec_module(cleanup_git_state)

run_command = cleanup_git_state.run_command
CleanupError = cleanup_git_state.CleanupError


class ReconcileError(RuntimeError):
    """Raised when reconciliation cannot make a conservative decision."""


ITEM_HEADER_RE = re.compile(r"^(?P<num>\d+)\.\s+\*\*")
ITEM_BRANCH_RE = re.compile(r"^vps-loop/item-(\d+)$")
TERMINAL_MARKERS = ("DONE", "MOOT", "DO NOT START")


@dataclass(frozen=True)
class Item:
    """One numbered backlog entry's location and unmodified header line."""

    number: int
    line_index: int
    header_line: str


@dataclass(frozen=True)
class MergedPR:
    """GitHub evidence that ``vps-loop/item-<number>`` already merged."""

    pr_number: int
    merged_date: str


def parse_items(lines: Sequence[str]) -> list[Item]:
    """Find every ``N. **...`` backlog item header, in file order.

    A continuation line is always indented, so it never matches this
    line-start anchored pattern — only the first line of each item does,
    regardless of which section (main backlog or a restored-entries
    section) it lives in.
    """

    items = []
    for index, line in enumerate(lines):
        match = ITEM_HEADER_RE.match(line)
        if match:
            items.append(Item(number=int(match.group("num")), line_index=index, header_line=line))
    return items


def has_terminal_marker(header_line: str) -> bool:
    """Return whether the item's bold header already opens with a status word."""

    bold_start = header_line.find("**")
    if bold_start == -1:
        return False
    remainder = header_line[bold_start + 2 :]
    return any(remainder.startswith(marker) for marker in TERMINAL_MARKERS)


def parse_merged_prs_payload(payload: list[dict[str, object]]) -> dict[int, MergedPR]:
    """Extract ``{item number: MergedPR}`` from a `gh pr list --json` payload.

    Pure and network-free so it can be unit tested directly; the only I/O is
    in `load_merged_item_prs` below.
    """

    merged: dict[int, MergedPR] = {}
    for entry in payload:
        branch = entry.get("headRefName")
        if not isinstance(branch, str):
            continue
        match = ITEM_BRANCH_RE.match(branch)
        if not match:
            continue
        number = int(match.group(1))
        pr_number = int(entry["number"])  # type: ignore[call-overload]
        merged_at = str(entry.get("mergedAt") or "")
        merged_date = merged_at.split("T", 1)[0] if merged_at else "unknown-date"
        existing = merged.get(number)
        # A merged branch name can't realistically be reused by a second PR,
        # but if it ever happens, keep the higher (more recent) PR number
        # rather than guessing from timestamp formatting.
        if existing is None or pr_number > existing.pr_number:
            merged[number] = MergedPR(pr_number=pr_number, merged_date=merged_date)
    return merged


def load_merged_item_prs(repo: Path) -> dict[int, MergedPR]:
    """Query GitHub once for every merged ``vps-loop/item-<N>`` PR."""

    result = run_command(
        ("gh", "pr", "list", "--state", "merged", "--limit", "1000", "--json", "number,headRefName,mergedAt"),
        cwd=repo,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReconcileError(f"gh pr list returned invalid JSON: {exc}") from exc
    return parse_merged_prs_payload(payload)


def reconcile_item_statuses(
    lines: Sequence[str], merged_by_item: dict[int, MergedPR]
) -> tuple[list[str], list[str], list[str]]:
    """Prefix a DONE marker onto any merged item whose header doesn't have one.

    Returns ``(new_lines, changes, warnings)``. A warning (not an error) is
    recorded for a merged branch whose item number has no matching header —
    e.g. a since-renumbered or manually removed entry — since guessing where
    to insert one would be exactly the kind of judgment call this script
    must not make unattended.
    """

    items_by_number = {item.number: item for item in parse_items(lines)}
    new_lines = list(lines)
    changes: list[str] = []
    warnings: list[str] = []
    for number, pr in sorted(merged_by_item.items()):
        item = items_by_number.get(number)
        if item is None:
            warnings.append(
                f"vps-loop/item-{number} has merged PR #{pr.pr_number}, but no numbered "
                f"backlog item {number} was found in the file"
            )
            continue
        line = new_lines[item.line_index]
        if has_terminal_marker(line):
            continue
        marker = f"DONE (PR #{pr.pr_number}, merged {pr.merged_date}) — "
        new_lines[item.line_index] = line.replace("**", "**" + marker, 1)
        changes.append(f"item {number}: marked DONE (PR #{pr.pr_number}, merged {pr.merged_date})")
    return new_lines, changes, warnings


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def find_headings(lines: Sequence[str]) -> list[tuple[int, int, str]]:
    """Return ``(line index, level, title)`` for every markdown heading, in order."""

    headings = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line.rstrip("\n"))
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))
    return headings


def _section_ends(headings: list[tuple[int, int, str]], total_lines: int) -> dict[int, int]:
    """Map each heading's line index to where its own section body ends.

    A section ends at the next heading whose level is less than or equal to
    its own — so a nested subheading (e.g. a ``### `` inside a ``## ``
    section) never prematurely ends its parent's section, but a same-level
    or higher-level heading (including one belonging to an entirely
    different, unrelated top-level section) always does.
    """

    ends: dict[int, int] = {}
    for position, (index, level, _title) in enumerate(headings):
        end = total_lines
        for _later_index, later_level, _later_title in headings[position + 1 :]:
            if later_level <= level:
                end = _later_index
                break
        ends[index] = end
    return ends


def merge_duplicate_level2_sections(lines: Sequence[str]) -> tuple[list[str], list[str]]:
    """Merge any ``## `` heading whose exact title repeats.

    The first occurrence keeps its heading; every later occurrence's body is
    appended to it (in original order) and that occurrence's own heading
    line is dropped. Only exact title matches are merged — a differently
    worded heading is left alone rather than guessed at. Section bounds
    account for every heading level, not just ``## ``, so an unrelated
    ``# `` section physically sitting between two duplicate ``## `` headings
    (as today's actual file layout has, with ``# Status log`` between the
    backlog and any restored-entries section) is never absorbed into the
    body being moved.
    """

    headings = find_headings(lines)
    level2 = [(index, title) for index, level, title in headings if level == 2]
    if len(level2) < 2:
        return list(lines), []

    section_end = _section_ends(headings, len(lines))

    by_title: dict[str, list[int]] = {}
    for index, title in level2:
        by_title.setdefault(title, []).append(index)
    duplicated = {title: indices for title, indices in by_title.items() if len(indices) > 1}
    if not duplicated:
        return list(lines), []

    skip_ranges: list[tuple[int, int]] = []
    inject_before: dict[int, list[str]] = {}
    changes: list[str] = []
    for title, indices in duplicated.items():
        first = indices[0]
        combined: list[str] = []
        for duplicate in indices[1:]:
            body = list(lines[duplicate + 1 : section_end[duplicate]])
            if combined:
                combined.append("\n")
            combined.extend(body)
            skip_ranges.append((duplicate, section_end[duplicate]))
        inject_before.setdefault(section_end[first], []).extend(combined)
        duplicate_line_numbers = ", ".join(str(index + 1) for index in indices[1:])
        changes.append(
            f"merged {len(indices) - 1} duplicate '## {title}' heading(s) "
            f"(originally at line(s) {duplicate_line_numbers}) into the first "
            f"occurrence at line {first + 1}"
        )

    def in_skip_range(index: int) -> bool:
        return any(start <= index < end for start, end in skip_ranges)

    new_lines: list[str] = []
    for index, line in enumerate(lines):
        # Injection must run even when this exact index is also a skip-range
        # start (the common two-occurrence case, where the duplicate heading
        # sits immediately after the first occurrence's original body with no
        # other section in between) — the two checks are independent.
        if index in inject_before:
            new_lines.extend(inject_before[index])
        if in_skip_range(index):
            continue
        new_lines.append(line)
    if len(lines) in inject_before:
        new_lines.extend(inject_before[len(lines)])

    return new_lines, changes


def main() -> int:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Any worktree in the target repository")
    parser.add_argument(
        "--file", type=Path, default=None, help="Path to NEXT_TASK.md (default: <repo>/NEXT_TASK.md)"
    )
    parser.add_argument("--apply", action="store_true", help="Write the reconciled file; default is a dry run")
    args = parser.parse_args()

    repo = args.repo.resolve()
    target = (args.file or repo / "NEXT_TASK.md").resolve()
    if not target.exists():
        print(f"ERROR: {target} does not exist", file=sys.stderr)
        return 2

    try:
        original_text = target.read_text(encoding="utf-8")
        lines = original_text.splitlines(keepends=True)
        merged_by_item = load_merged_item_prs(repo)
    except (ReconcileError, CleanupError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    deduplicated_lines, dedupe_changes = merge_duplicate_level2_sections(lines)
    reconciled_lines, status_changes, warnings = reconcile_item_statuses(deduplicated_lines, merged_by_item)

    for change in dedupe_changes:
        print(f"DEDUPLICATE: {change}")
    for change in status_changes:
        print(f"RECONCILE:   {change}")
    for warning in warnings:
        print(f"WARNING:     {warning}", file=sys.stderr)

    if not dedupe_changes and not status_changes:
        print("Nothing to reconcile.")
        return 0

    if args.apply:
        target.write_text("".join(reconciled_lines), encoding="utf-8")
        print(f"Applied {len(dedupe_changes)} section merge(s) and {len(status_changes)} status update(s).")
    else:
        print("Dry run only. Re-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
