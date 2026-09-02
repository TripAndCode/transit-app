"""Detect comment-policy violations in Python and TypeScript sources."""

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

_PREFIX_BY_SUFFIX = {".py": "#", ".ts": "//", ".tsx": "//", ".js": "//", ".jsx": "//"}

# Tooling directives, not prose — they say nothing that can go stale.
_PRAGMA = re.compile(
    r"^(?:noqa|type:|fmt:|pylint|isort:|mypy:|-\*-|!|"
    r"eslint|@ts-|prettier|biome-|v8 ignore|istanbul )",
)

_BANNER = re.compile(r"^[=\-*_#~]{4,}$")

# A pointer at another piece of prose. Pointing at a symbol is fine — that
# survives a rename via grep; pointing at a comment breaks silently when the
# other comment moves or gets rewritten.
_XREF = re.compile(
    r"\b(?:see|per|refer to|described in|explained in|noted in)\b.*"
    r"\b(?:comments?|docstrings?|docs?|notes?)\b"
    r"|\bas\s+(?:noted|described|mentioned|explained)\b.*\b(?:above|below)\b"
    r"|\b(?:comments?|docstrings?|docs?|notes?)\s+(?:above|below)\b",
    re.IGNORECASE,
)

_LINE_REF = re.compile(r"\blines?\s+\d+", re.IGNORECASE)

_DIFF_FILE = re.compile(r"^\+\+\+ b/(.*)$")
_DIFF_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")

_LINE_RULES = (
    ("banner", _BANNER, "banner comment"),
    ("comment-xref", _XREF, "reference to another comment"),
    ("line-ref", _LINE_REF, "reference to a line number"),
)


@dataclass(frozen=True)
class Violation:
    """A single policy breach, anchored to the line that starts it."""

    line: int
    rule: str
    message: str


def _prefix_for(path):
    for suffix, prefix in _PREFIX_BY_SUFFIX.items():
        if path.endswith(suffix):
            return prefix
    return None


def _comment_body(line, prefix):
    """Return the prose after `prefix`, or None if this line isn't prose."""
    stripped = line.lstrip()
    if not stripped.startswith(prefix):
        return None
    body = stripped[len(prefix) :].strip()
    return None if _PRAGMA.match(body) else body


def find_violations(path, lines, max_block=6, only_lines=None):
    """Return policy violations for `lines`, the contents of `path`.

    `only_lines`, when given, keeps just the violations anchored on one of
    those line numbers — the added lines of a diff, so a pre-existing breach
    doesn't fail a branch that merely edited nearby.
    """
    prefix = _prefix_for(path)
    if prefix is None:
        return []

    violations = []
    start = None
    run = 0

    def close_block():
        if run > max_block:
            violations.append(Violation(start, "long-block", f"{run}-line comment block"))

    for number, line in enumerate(lines, start=1):
        body = _comment_body(line, prefix)
        if body is None:
            close_block()
            run = 0
            continue
        if run == 0:
            start = number
        run += 1
        for rule, pattern, message in _LINE_RULES:
            if pattern.search(body):
                violations.append(Violation(number, rule, message))
    close_block()
    if only_lines is not None:
        violations = [v for v in violations if v.line in only_lines]
    return sorted(violations, key=lambda v: (v.line, v.rule))


def stale_candidates(path, lines, changed, radius=3):
    """Return comments left untouched beside a changed line.

    These are the only comments worth spending a semantic review on: the code
    they sit against moved, and they did not.
    """
    prefix = _prefix_for(path)
    if prefix is None:
        return []

    near = {n for line in changed for n in range(line - radius, line + radius + 1)}
    return [
        Violation(number, "stale-candidate", "unchanged comment beside changed code")
        for number, line in enumerate(lines, start=1)
        if number in near and number not in changed and _comment_body(line, prefix) is not None
    ]


def parse_added_lines(diff_text):
    """Map each file in a `git diff -U0` to the line numbers it gained."""
    added = {}
    path = None
    for line in diff_text.splitlines():
        header = _DIFF_FILE.match(line)
        if header:
            path = header.group(1)
            continue
        hunk = _DIFF_HUNK.match(line)
        if hunk and path:
            start = int(hunk.group(1))
            count = int(hunk.group(2)) if hunk.group(2) is not None else 1
            if count:
                added.setdefault(path, set()).update(range(start, start + count))
    return added


_AUTO_BASES = ("origin/HEAD", "master", "main")


def resolve_base(requested, exists):
    """Pick the ref to diff against, or None when there is nothing to compare.

    Returning None is not a failure: a repo with no such ref simply has no
    baseline here, and the gate must let the push through rather than block on
    a git error.
    """
    candidates = _AUTO_BASES if requested == "auto" else (requested,)
    return next((ref for ref in candidates if exists(ref)), None)


def exit_code(count, gating, warn_only):
    """Return 2 only when a blocking gate run actually found something."""
    return 2 if (count and gating and not warn_only) else 0


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _ref_exists(root):
    def check(ref):
        return (
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=root,
                capture_output=True,
            ).returncode
            == 0
        )

    return check


def _read(root, path):
    full = os.path.join(root, path)
    try:
        with open(full, encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()
    except (OSError, IsADirectoryError):
        return None


def _tracked_sources(root):
    globs = ["*.py", "*.ts", "*.tsx", "*.js", "*.jsx"]
    return [p for p in _git(["ls-files", *globs], root).splitlines() if p]


def _report(hits, root, label, limit=None):
    """Print findings. `limit=None` prints all of them.

    A mode whose output another process consumes as its complete work list must
    not be truncated: a dropped row there reads as nothing to review.
    """
    if not hits:
        print(f"{label}: clean")
        return 0
    by_rule = Counter(rule for _, v in hits for rule in [v.rule])
    print(f"{label}: {len(hits)} finding(s)")
    for rule, count in by_rule.most_common():
        print(f"  {rule:<16} {count}")
    print()
    shown = hits if limit is None else hits[:limit]
    for path, v in shown:
        print(f"  {path}:{v.line}  [{v.rule}] {v.message}")
    if limit is not None and len(hits) > limit:
        print(f"  ... and {len(hits) - limit} more")
    return len(hits)


def main(argv=None):
    """Run the comment policy over a repo, a diff, or a stale-comment sweep."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--max-block", type=int, default=6)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline", action="store_true", help="scan every tracked source")
    mode.add_argument(
        "--diff",
        metavar="BASE",
        nargs="?",
        const="auto",
        help="only lines added since BASE ('auto': origin/HEAD, master, main)",
    )
    mode.add_argument("--stale-candidates", metavar="BASE", dest="stale")
    parser.add_argument("--radius", type=int, default=3)
    parser.add_argument("--warn", action="store_true", help="report but never fail the gate")
    args = parser.parse_args(argv)

    root = args.root
    if args.baseline:
        hits = []
        for path in _tracked_sources(root):
            lines = _read(root, path)
            if lines is None:
                continue
            hits += [(path, v) for v in find_violations(path, lines, args.max_block)]
        _report(hits, root, "baseline", limit=40)
        return 0

    requested = args.diff or args.stale
    base = resolve_base(requested, _ref_exists(root))
    if base is None:
        print(f"comment policy: no baseline ref for {requested!r} — skipped")
        return 0
    try:
        diff = _git(["diff", "-U0", f"{base}...HEAD"], root)
    except (subprocess.CalledProcessError, OSError) as error:
        print(f"comment policy: skipped ({error.__class__.__name__})")
        return 0
    added = parse_added_lines(diff)

    hits = []
    for path, lines_added in sorted(added.items()):
        lines = _read(root, path)
        if lines is None:
            continue
        if args.stale:
            hits += [(path, c) for c in stale_candidates(path, lines, lines_added, args.radius)]
        else:
            hits += [(path, v) for v in find_violations(path, lines, args.max_block, only_lines=lines_added)]
    count = _report(
        hits,
        root,
        "stale candidates" if args.stale else "comment policy",
        limit=None if args.stale else 40,
    )
    return exit_code(count, gating=bool(args.diff), warn_only=args.warn)


if __name__ == "__main__":
    sys.exit(main())
