#!/usr/bin/env python3
"""Collect the GitHub operations-status snapshot for the `github` component of
`ops_status.py`'s `ComponentStatus` contract.

Reuses the VPS's existing `gh` CLI authentication (the same one `/vps-loop-run` and
`scripts/cleanup_git_state.py`/`reconcile_next_task.py` already rely on) rather than
managing a separate credential. Two bounded, single-page API calls per collection --
`gh pr list` for open PRs (draft state, mergeability, CI-check rollup) and one
`repos/{slug}/branches` page for branch protection flags -- plus one local `git
for-each-ref` (no GitHub API cost at all) to find non-protected, long-untouched
branches. None of these calls paginate past their own `--limit`/`per_page`: a repo
with more open PRs or branches than that bound is undercounted (surfaced via
`*_truncated` detail flags), never silently retried into an unbounded scan.

`gh api` defaults to a POST request the moment any `-f`/`-F` parameter is given
(its way of saying "you're sending data"), so query parameters here are always
inlined into the URL (`?per_page=...`) rather than passed as `-F` flags -- the
latter would silently turn a read into a write against a live GitHub repo.

A failed live collection never fabricates a healthy status. It falls back to the
last cached successful document (`--cache-file`, default `<repo>/.cache/ops-status-
github.json`) so a transient outage degrades through the contract's own age-based
`healthy`/`degraded`/`stale` states instead of losing all signal immediately; with
no cache at all (e.g. the very first run, or a persistently broken environment) it
reports `unknown`, per the same "never a false green" rule `ops_status.py` already
enforces for a component with no observed success. `diagnose_gh_failure` classifies
a failing `gh` invocation into a small closed set of labels (`auth_error`,
`rate_limited`, `network_error`, `not_found`, `unknown_error`) and a short
`redact_secrets`-scrubbed excerpt of its stderr -- never the raw stderr itself,
which is both how `ops_status.validate_details` already forbids a raw log/stderr
key and defense in depth against a misconfigured environment echoing token
material back on the command line.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """Load another `scripts/*.py` module by file path (see `collect_vps_status.py`'s
    identical rationale: `scripts/` has no `__init__.py`)."""

    spec = importlib.util.spec_from_file_location(name, _SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ops_status = _load_sibling("ops_status")


DEFAULT_PR_LIMIT = 50
DEFAULT_BRANCH_LIMIT = 100
DEFAULT_STALE_BRANCH_DAYS = 30.0
DEFAULT_POLL_INTERVAL_SECONDS = 3600.0
# Healthy immediately after a fresh success; past this, a cached document is aging
# but still plausibly fine. Mirrors collect_vps_status.py's tick-interval-multiplier
# pattern so a missed poll or two doesn't itself read as a problem.
DEFAULT_HEALTHY_MULTIPLIER = 1.5
DEFAULT_STALE_MULTIPLIER = 6.0
DEFAULT_ALWAYS_PROTECTED = frozenset({"main", "production"})
DEFAULT_CACHE_BASENAME = Path(".cache") / "ops-status-github.json"

# GitHub's own conclusion values that mean "this check did not pass" -- SUCCESS,
# NEUTRAL, SKIPPED, and STALE are all "did not block the PR", not a failure.
FAILING_CHECK_CONCLUSIONS = frozenset({"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"})

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

_SLUG_RE = re.compile(r"github\.com[:/]+(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")

# GitHub personal/installation/OAuth token prefixes (ghp_/gho_/ghs_/ghr_/ghu_ and the
# newer github_pat_ format), plus a generic bearer-auth header -- scrubbed from any
# free-form text before it can reach a status document's `details` or the CLI.
_TOKEN_PATTERN = re.compile(
    r"(?:ghp|gho|ghs|ghr|ghu)_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._\-]{20,}"
)


def redact_secrets(text: str) -> str:
    """Scrub GitHub token material from free-form diagnostic text (see module docstring)."""

    return _TOKEN_PATTERN.sub("[REDACTED]", text)


def _run(cmd: Sequence[str], *, timeout: float = 20.0) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _parse_github_slug(url: str) -> str | None:
    match = _SLUG_RE.search(url.strip())
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('name')}"


def resolve_repo_slug(repo: Path, *, git_runner: Runner = _run) -> str | None:
    """Return `"owner/name"` parsed from the `origin` remote URL, or `None` if it can't be
    determined (not a git repo, no `origin`, or a non-GitHub remote)."""

    try:
        proc = git_runner(["git", "-C", str(repo), "remote", "get-url", "origin"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _parse_github_slug(proc.stdout)


def diagnose_gh_failure(stderr: str) -> tuple[str, str]:
    """Classify a failed `gh` invocation's stderr into `(kind, redacted_excerpt)`.

    `kind` is one of a small closed set safe to store verbatim in a status
    document; `redacted_excerpt` is `redact_secrets(stderr)` truncated to the
    contract's per-string bound, kept short and scrubbed rather than the raw
    stderr (which `ops_status.validate_details` forbids as a key shape anyway,
    and which could otherwise echo token material back from a misconfigured
    environment).
    """

    lowered = stderr.lower()
    if "bad credentials" in lowered or "not logged" in lowered or "authentication" in lowered or "401" in lowered:
        kind = "auth_error"
    elif "rate limit" in lowered or "403" in lowered:
        kind = "rate_limited"
    elif any(marker in lowered for marker in ("could not resolve host", "timed out", "timeout", "connection")):
        kind = "network_error"
    elif "404" in lowered or "not found" in lowered:
        kind = "not_found"
    else:
        kind = "unknown_error"
    excerpt = redact_secrets(stderr.strip())[: ops_status.MAX_DETAIL_STRING_LENGTH]
    return kind, excerpt


def fetch_open_prs(*, repo_slug: str, limit: int, runner: Runner) -> tuple[list[dict] | None, str | None, str]:
    """Return `(prs, error_kind, error_detail)`. `prs` is the raw parsed `gh pr list --json`
    payload, or `None` on any failure (`error_kind`/`error_detail` then describe why)."""

    try:
        proc = runner(
            [
                "gh",
                "pr",
                "list",
                "-R",
                repo_slug,
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,isDraft,mergeable,mergeStateStatus,statusCheckRollup",
            ]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "gh_unavailable", redact_secrets(str(exc))[: ops_status.MAX_DETAIL_STRING_LENGTH]
    if proc.returncode != 0:
        kind, excerpt = diagnose_gh_failure(proc.stderr)
        return None, kind, excerpt
    try:
        prs = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "invalid_response", ""
    if not isinstance(prs, list):
        return None, "invalid_response", ""
    return prs, None, ""


def fetch_branch_protection(*, repo_slug: str, limit: int, runner: Runner) -> tuple[dict[str, bool], bool] | None:
    """Return `({branch_name: protected}, page_truncated)` for up to `limit` branches
    (one bounded page, via a URL query param -- never `-f`/`-F`, which would flip
    `gh api` to POST), or `None` on any failure.

    `page_truncated` is True when the page came back with `>= limit` entries, meaning
    the repo may have more branches than fit on this one page. A caller must not treat
    a branch name absent from the mapping as confirmed-unprotected when `page_truncated`
    is True -- it may simply not have been on this page (see `gather_stale_branches`).
    """

    try:
        proc = runner(["gh", "api", f"repos/{repo_slug}/branches?per_page={limit}"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    mapping: dict[str, bool] = {}
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            mapping[item["name"]] = bool(item.get("protected", False))
    return mapping, len(data) >= limit


def gather_stale_branches(
    *,
    repo: Path,
    now: datetime,
    protected_names: frozenset[str],
    remote_protection: Mapping[str, bool] | None,
    stale_days: float,
    max_branches: int,
    git_runner: Runner = _run,
    remote_protection_truncated: bool = False,
) -> tuple[str, ...] | None:
    """Return up to `max_branches` non-protected branch names whose last commit is at
    least `stale_days` old (oldest first), or `None` if the underlying git call failed.

    Uses local git (already-fetched remote-tracking refs), not a per-branch GitHub API
    call -- the branches API alone doesn't return a last-commit date, and fetching it
    per branch would turn one bounded call into an unbounded one as branch count grows.
    A branch is treated as protected if GitHub reports it so, or if its name is in
    `protected_names` (this repo does not currently configure branch protection at all,
    so the latter is the operative check in practice -- see `CLAUDE.md`). A branch name
    absent from `remote_protection` is treated as unprotected only when
    `remote_protection_truncated` is False; when the branches page was truncated, an
    absent name's protection status is unknown (it may simply be past the page limit),
    so it is excluded from the stale-branches list rather than assumed unprotected.
    """

    try:
        proc = git_runner(
            [
                "git",
                "-C",
                str(repo),
                "for-each-ref",
                "--format=%(refname:short)\t%(committerdate:iso-strict)",
                "refs/remotes/origin",
            ]
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None

    stale: list[tuple[float, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        name, sep, date_str = line.partition("\t")
        if not sep:
            continue
        if name == "origin":
            continue  # the `origin/HEAD` symref resolves to bare "origin", not a branch
        name = name.removeprefix("origin/")
        if not name or name == "HEAD":
            continue
        if name in protected_names:
            continue
        if remote_protection is not None:
            if name in remote_protection:
                if remote_protection[name]:
                    continue
            elif remote_protection_truncated:
                continue  # protection status unknown past the truncated page -- don't assume unprotected
        try:
            commit_dt = datetime.fromisoformat(date_str.strip())
        except ValueError:
            continue
        commit_dt = commit_dt.astimezone(timezone.utc)
        age_days = (now - commit_dt).total_seconds() / 86400.0
        if age_days >= stale_days:
            stale.append((age_days, name))

    stale.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(name for _, name in stale[:max_branches])


def summarize_prs(prs: Sequence[dict], *, limit: int) -> dict[str, object]:
    """Reduce a raw `gh pr list --json` payload to the bounded, contract-safe counts and
    (capped) PR-number lists this component's `details` carries."""

    draft_count = 0
    conflicting_numbers: list[int] = []
    failing_numbers: list[int] = []

    for pr in prs:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if pr.get("isDraft"):
            draft_count += 1
        is_conflicting = pr.get("mergeable") == "CONFLICTING" or pr.get("mergeStateStatus") == "DIRTY"
        if is_conflicting and isinstance(number, int):
            conflicting_numbers.append(number)
        checks = pr.get("statusCheckRollup")
        if isinstance(checks, list) and isinstance(number, int):
            failing = any(isinstance(c, dict) and c.get("conclusion") in FAILING_CHECK_CONCLUSIONS for c in checks)
            if failing:
                failing_numbers.append(number)

    conflicting_numbers.sort()
    failing_numbers.sort()

    return {
        "open_pr_count": len(prs),
        "open_pr_list_truncated": len(prs) >= limit,
        "draft_pr_count": draft_count,
        "conflicting_pr_count": len(conflicting_numbers),
        "conflicting_pr_numbers": conflicting_numbers[: ops_status.MAX_DETAIL_LIST_LENGTH],
        "failing_checks_pr_count": len(failing_numbers),
        "failing_checks_pr_numbers": failing_numbers[: ops_status.MAX_DETAIL_LIST_LENGTH],
    }


def load_cached_document(cache_path: Path) -> dict | None:
    """Best-effort load of the last cached status document. Any problem (missing file,
    unreadable, invalid JSON, or contract-invalid) is treated as "no cache" rather than
    raised -- a corrupt cache must never block collection."""

    try:
        raw = cache_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    try:
        ops_status.validate_document(data)
    except ops_status.OpsStatusError:
        return None
    return data


def save_cached_document(cache_path: Path, document: dict) -> None:
    """Best-effort write-then-rename; a cache write failure must never abort collection,
    since the cache is a resilience aid, not the primary output."""

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(cache_path.name + ".tmp")
        tmp_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        tmp_path.replace(cache_path)
    except OSError:
        pass


@dataclass(frozen=True)
class GithubFacts:
    """Every raw fact `build_github_status` needs, gathered once by `collect_github_facts`.

    Kept separate from the gathering step so state-transition tests can construct this
    directly instead of mocking subprocess calls (see `collect_vps_status.py`'s
    identical `VpsFacts` split)."""

    now: datetime
    prs: list[dict] | None
    pr_limit: int
    pr_error_kind: str | None
    pr_error_detail: str
    branch_protection_known: bool
    branch_page_truncated: bool
    stale_branches: tuple[str, ...] | None
    cached_document: dict | None


def build_github_status(
    facts: GithubFacts,
    *,
    healthy_max_age_seconds: float,
    stale_max_age_seconds: float,
):
    """Build and validate the `github` component's `ComponentStatus` from already-gathered
    `facts`. See the module docstring for the fresh-success / cached-fallback / no-cache
    three-way split."""

    if facts.prs is not None:
        cached_stale_branches: object = ()
        if facts.cached_document is not None:
            cached_stale_branches = ops_status.from_json_dict(facts.cached_document).details.get(
                "stale_unprotected_branches", ()
            )

        stale_unprotected_branches = (
            list(facts.stale_branches)
            if facts.stale_branches is not None
            else [name for name in cached_stale_branches if isinstance(name, str)]
            if isinstance(cached_stale_branches, list)
            else []
        )
        details = {
            **summarize_prs(facts.prs, limit=facts.pr_limit),
            "branch_protection_known": facts.branch_protection_known,
            "branch_page_truncated": facts.branch_page_truncated,
            "stale_branches_known": facts.stale_branches is not None,
            "stale_unprotected_branches": stale_unprotected_branches,
        }
        return ops_status.build_status(
            component="github",
            observed_at=facts.now,
            last_success_at=facts.now,
            healthy_max_age_seconds=healthy_max_age_seconds,
            stale_max_age_seconds=stale_max_age_seconds,
            reported_failure=False,
            details=details,
            now=facts.now,
        )

    if facts.cached_document is not None:
        cached = ops_status.from_json_dict(facts.cached_document)
        details = dict(cached.details)
        details["branch_protection_known"] = facts.branch_protection_known
        details["branch_page_truncated"] = facts.branch_page_truncated
        if facts.stale_branches is not None:
            details["stale_unprotected_branches"] = list(facts.stale_branches)
        details["stale_branches_known"] = facts.stale_branches is not None
        details["last_error_kind"] = facts.pr_error_kind or "unknown_error"
        if facts.pr_error_detail:
            details["last_error_detail"] = facts.pr_error_detail
        return ops_status.build_status(
            component="github",
            observed_at=facts.now,
            last_success_at=cached.last_success_at,
            healthy_max_age_seconds=healthy_max_age_seconds,
            stale_max_age_seconds=stale_max_age_seconds,
            reported_failure=False,
            details=details,
            now=facts.now,
        )

    details = {
        "branch_protection_known": facts.branch_protection_known,
        "branch_page_truncated": facts.branch_page_truncated,
        "stale_branches_known": facts.stale_branches is not None,
        "stale_unprotected_branches": list(facts.stale_branches or ()),
        "last_error_kind": facts.pr_error_kind or "unknown_error",
    }
    if facts.pr_error_detail:
        details["last_error_detail"] = facts.pr_error_detail
    return ops_status.build_status(
        component="github",
        observed_at=facts.now,
        last_success_at=None,
        healthy_max_age_seconds=healthy_max_age_seconds,
        stale_max_age_seconds=stale_max_age_seconds,
        reported_failure=False,
        details=details,
        now=facts.now,
    )


def collect_github_facts(
    *,
    repo: Path,
    pr_limit: int = DEFAULT_PR_LIMIT,
    branch_limit: int = DEFAULT_BRANCH_LIMIT,
    stale_branch_days: float = DEFAULT_STALE_BRANCH_DAYS,
    always_protected: frozenset[str] = DEFAULT_ALWAYS_PROTECTED,
    cache_path: Path | None = None,
    now: datetime | None = None,
    gh_runner: Runner = _run,
    git_runner: Runner = _run,
) -> GithubFacts:
    """Gather every raw fact for `repo`'s GitHub status, doing the actual `gh`/git I/O."""

    now = now or datetime.now(timezone.utc)

    prs: list[dict] | None
    pr_error_kind: str | None
    branch_protection: dict[str, bool] | None
    branch_page_truncated: bool = False

    repo_slug = resolve_repo_slug(repo, git_runner=git_runner)
    if repo_slug is None:
        prs, pr_error_kind, pr_error_detail = None, "repo_slug_unresolved", ""
        branch_protection = None
    else:
        prs, pr_error_kind, pr_error_detail = fetch_open_prs(repo_slug=repo_slug, limit=pr_limit, runner=gh_runner)
        branch_protection_result = fetch_branch_protection(repo_slug=repo_slug, limit=branch_limit, runner=gh_runner)
        if branch_protection_result is not None:
            branch_protection, branch_page_truncated = branch_protection_result
        else:
            branch_protection = None

    stale_branches = gather_stale_branches(
        repo=repo,
        now=now,
        protected_names=always_protected,
        remote_protection=branch_protection,
        remote_protection_truncated=branch_page_truncated,
        stale_days=stale_branch_days,
        max_branches=ops_status.MAX_DETAIL_LIST_LENGTH,
        git_runner=git_runner,
    )

    cached_document = load_cached_document(cache_path) if cache_path is not None else None

    return GithubFacts(
        now=now,
        prs=prs,
        pr_limit=pr_limit,
        pr_error_kind=pr_error_kind,
        pr_error_detail=pr_error_detail,
        branch_protection_known=branch_protection is not None,
        branch_page_truncated=branch_page_truncated,
        stale_branches=stale_branches,
        cached_document=cached_document,
    )


def collect_github_status(
    *,
    cache_path: Path | None = None,
    healthy_multiplier: float = DEFAULT_HEALTHY_MULTIPLIER,
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    **facts_kwargs,
):
    """`collect_github_facts` + `build_github_status` in one call, persisting the result to
    `cache_path` (if given) only when this run's own live collection actually succeeded --
    a cache-fallback result must never overwrite a still-good cache with a copy of itself
    stamped with a newer, misleadingly-fresh `observed_at`."""

    facts = collect_github_facts(cache_path=cache_path, **facts_kwargs)
    status = build_github_status(
        facts,
        healthy_max_age_seconds=poll_interval_seconds * healthy_multiplier,
        stale_max_age_seconds=poll_interval_seconds * stale_multiplier,
    )
    if facts.prs is not None and cache_path is not None:
        save_cached_document(cache_path, ops_status.to_json_dict(status))
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: prints the `github` component's status document as JSON.

    Exit code: 0 when `healthy`/`degraded`, 1 when `stale`/`failed`/`unknown` (mirrors
    `collect_vps_status.py`'s "1 means this needs attention" convention), 2 on a hard
    error (a status document that fails contract validation -- collection failures
    themselves never raise; they degrade to `unknown` or a cached fallback instead).
    """

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Any worktree in the target repository")
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=None,
        help=f"Path to persist/reload the last status document (default: <repo>/{DEFAULT_CACHE_BASENAME})",
    )
    parser.add_argument("--pr-limit", type=int, default=DEFAULT_PR_LIMIT)
    parser.add_argument("--branch-limit", type=int, default=DEFAULT_BRANCH_LIMIT)
    parser.add_argument("--stale-branch-days", type=float, default=DEFAULT_STALE_BRANCH_DAYS)
    parser.add_argument("--poll-interval-seconds", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--healthy-multiplier", type=float, default=DEFAULT_HEALTHY_MULTIPLIER)
    parser.add_argument("--stale-multiplier", type=float, default=DEFAULT_STALE_MULTIPLIER)
    parser.add_argument(
        "--always-protected",
        default=",".join(sorted(DEFAULT_ALWAYS_PROTECTED)),
        help="Comma-separated branch names treated as protected regardless of GitHub's own report",
    )
    parser.add_argument("--out", type=Path, default=None, help="Also write the status document as JSON to this path")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    cache_path = (args.cache_file or (repo / DEFAULT_CACHE_BASENAME)).resolve()
    always_protected = frozenset(name.strip() for name in args.always_protected.split(",") if name.strip())

    try:
        status = collect_github_status(
            repo=repo,
            cache_path=cache_path,
            pr_limit=args.pr_limit,
            branch_limit=args.branch_limit,
            stale_branch_days=args.stale_branch_days,
            always_protected=always_protected,
            poll_interval_seconds=args.poll_interval_seconds,
            healthy_multiplier=args.healthy_multiplier,
            stale_multiplier=args.stale_multiplier,
        )
    except ops_status.OpsStatusError as exc:
        print(f"ERROR: status document failed contract validation: {redact_secrets(str(exc))}", file=sys.stderr)
        return 2

    document = ops_status.to_json_dict(status)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document, indent=2))

    return 0 if status.state in ("healthy", "degraded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
