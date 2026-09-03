#!/usr/bin/env python3
"""Build a secret-aware review diff and a compact machine-readable manifest."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_PATTERNS = (
    "poetry.lock",
    "frontend/package-lock.json",
    "*.env",
    "*.env.local",
    "*.env.*.local",
    "*.pem",
    "*.p12",
    "*service-account*.json",
    "*credentials.json",
)


@dataclass(frozen=True)
class Change:
    """One numstat entry used for deterministic review sizing."""

    path: str
    added: int | None
    deleted: int | None

    @property
    def lines(self) -> int:
        """Return textual changed lines; binary entries contribute zero."""

        return (self.added or 0) + (self.deleted or 0)


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git in ``repo`` without relying on the caller's working directory."""

    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=check,
        capture_output=True,
        text=True,
    )


def is_excluded(path: str, patterns: tuple[str, ...] = EXCLUDED_PATTERNS) -> bool:
    """Return whether a path may carry generated or credential material."""

    name = Path(path).name
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)


def parse_numstat(output: str) -> list[Change]:
    """Parse ``git diff --numstat --no-renames`` output."""

    changes: list[Change] = []
    for entry in output.split("\0"):
        if not entry:
            continue
        added, deleted, path = entry.split("\t", 2)
        changes.append(
            Change(
                path=path,
                added=None if added == "-" else int(added),
                deleted=None if deleted == "-" else int(deleted),
            )
        )
    return changes


def diff_numstat(repo: Path, merge_base: str, patterns: tuple[str, ...] = EXCLUDED_PATTERNS) -> list[Change]:
    """Return the final tracked changes relative to the branch merge-base."""

    result = run_git(repo, "diff", "--numstat", "-z", "--no-renames", merge_base)
    return [change for change in parse_numstat(result.stdout) if not is_excluded(change.path, patterns)]


def untracked_changes(repo: Path, patterns: tuple[str, ...] = EXCLUDED_PATTERNS) -> list[Change]:
    """Count non-ignored, non-sensitive untracked files without touching the index."""

    result = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    changes: list[Change] = []
    for path in filter(None, result.stdout.split("\0")):
        if is_excluded(path, patterns):
            continue
        candidate = repo / path
        line_count = 1 if candidate.is_symlink() else len(candidate.read_bytes().splitlines())
        changes.append(Change(path=path, added=line_count, deleted=0))
    return changes


def changed_paths(
    repo: Path,
    merge_base: str,
    patterns: tuple[str, ...] = EXCLUDED_PATTERNS,
) -> tuple[list[str], list[str]]:
    """Return included paths and deliberately excluded paths without reading secrets."""

    paths: set[str] = set()
    paths.update(
        filter(
            None,
            run_git(repo, "diff", "--name-only", "-z", "--no-renames", merge_base).stdout.split("\0"),
        )
    )
    paths.update(
        filter(
            None,
            run_git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0"),
        )
    )
    return (
        sorted(path for path in paths if not is_excluded(path, patterns)),
        sorted(path for path in paths if is_excluded(path, patterns)),
    )


def append_diff(output: Path, result: subprocess.CompletedProcess[str]) -> None:
    """Append a Git diff result, accepting ``--no-index``'s changed exit status."""

    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(result.stdout)


def build_diff(
    repo: Path,
    merge_base: str,
    output_dir: Path,
    included: list[str],
    patterns: tuple[str, ...] = EXCLUDED_PATTERNS,
) -> Path:
    """Write committed, untracked, unstaged, and staged changes exactly once."""

    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="branch-review-", suffix=".diff", dir=output_dir)
    os.close(descriptor)
    output = Path(name)
    if included:
        output.write_text(
            run_git(
                repo,
                "diff",
                "--no-renames",
                merge_base,
                "--",
                *included,
            ).stdout,
            encoding="utf-8",
        )
        for change in untracked_changes(repo, patterns):
            append_diff(
                output,
                run_git(
                    repo,
                    "diff",
                    "--no-index",
                    "/dev/null",
                    change.path,
                    check=False,
                ),
            )
    return Path(name).resolve()


def _is_process_doc_path(path: str) -> bool:
    """CLAUDE.md and everything under .claude/** are executable process docs,
    not ordinary prose -- see CLAUDE.md's own "Git and pull requests" section."""
    return path == "CLAUDE.md" or path.startswith(".claude/")


def suggested_tier(paths: list[str]) -> str:
    """Classify obvious path-only tiers; the coordinator still checks semantics."""

    if not paths:
        return "empty"
    if all(_is_process_doc_path(path) for path in paths):
        return "process-doc"
    if all(path.endswith(".md") for path in paths):
        if any(_is_process_doc_path(path) for path in paths):
            return "process-doc"
        return "trivial"
    return "standard"


def is_test_path(path: str) -> bool:
    """Return whether changed lines belong to backend or frontend tests."""

    name = Path(path).name
    return path.startswith("tests/") or (
        path.startswith("frontend/src/") and any(marker in name for marker in (".test.", ".spec."))
    )


# Quality-gate paths: hooks/CI wiring, lint/type config, and any file whose
# own basename marks it as a `check-*`/`check_*` gate (a repo-wide naming
# convention, not just top-level scripts/ — e.g. frontend/scripts/check-
# entry-chunk.mjs and its test), plus specific non-`check-`-named
# deletion-safety scripts and this review script itself (a diff that quietly
# weakens this very list is exactly the class of change enforcement review
# exists to catch), along with each of those scripts' own tests, since a
# weakened test is just as dangerous as a weakened script. `is_excluded`'s
# path-or-basename fnmatch already does exactly the matching this needs, so
# it's reused rather than duplicated here.
ENFORCEMENT_PATTERNS: tuple[str, ...] = (
    ".claude/hooks/*",
    ".claude/settings.json",
    ".github/workflows/*",
    ".pre-commit-config.yaml",
    "pyproject.toml",
    "frontend/eslint.config.js",
    "frontend/package.json",
    "check-*",
    "check_*",
    "scripts/cleanup_git_state.py",
    "scripts/daily_git_hygiene.py",
    "scripts/prepare_review.py",
    "tests/unit/test_prepare_review.py",
    "tests/unit/test_cleanup_git_state.py",
    "tests/unit/test_daily_git_hygiene.py",
)


def touches_enforcement(path: str, patterns: tuple[str, ...] = ENFORCEMENT_PATTERNS) -> bool:
    """Return whether a path changes an automated quality gate."""

    return is_excluded(path, patterns)


def aggregate_changes(changes: list[Change]) -> dict[str, Change]:
    """Combine committed and local numstat entries by path."""

    totals: dict[str, Change] = {}
    for change in changes:
        previous = totals.get(change.path)
        if previous is None:
            totals[change.path] = change
            continue
        totals[change.path] = Change(
            path=change.path,
            added=None if previous.added is None or change.added is None else previous.added + change.added,
            deleted=None if previous.deleted is None or change.deleted is None else previous.deleted + change.deleted,
        )
    return totals


def main() -> int:
    """Prepare the review artifacts and print the manifest as JSON."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="main")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional basename or repository-relative glob to exclude",
    )
    args = parser.parse_args()

    repo = args.repo.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir == repo or repo in output_dir.parents:
        parser.error("--output-dir must be outside the reviewed repository")
    patterns = EXCLUDED_PATTERNS + tuple(args.exclude)
    run_git(repo, "rev-parse", "--verify", args.base)
    merge_base = run_git(repo, "merge-base", args.base, "HEAD").stdout.strip()
    included, excluded = changed_paths(repo, merge_base, patterns)
    combined = aggregate_changes(diff_numstat(repo, merge_base, patterns) + untracked_changes(repo, patterns))
    diff_path = build_diff(repo, merge_base, output_dir, included, patterns)
    total_lines = sum(change.lines for change in combined.values())
    test_lines = sum(change.lines for change in combined.values() if is_test_path(change.path))

    manifest = {
        "base": args.base,
        "merge_base": merge_base,
        "head": run_git(repo, "rev-parse", "HEAD").stdout.strip(),
        "repo": str(repo),
        "diff_path": str(diff_path),
        "changed_files": included,
        "deliberately_excluded": excluded,
        "changed_lines": total_lines,
        "test_lines": test_lines,
        "test_share": round(test_lines / total_lines, 4) if total_lines else 0,
        "suggested_tier": suggested_tier(included),
        "enforcement": any(touches_enforcement(path) for path in included),
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
