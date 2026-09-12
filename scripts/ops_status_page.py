#!/usr/bin/env python3
"""Assemble the four operations-status collectors (items 119-122) into one combined,
read-only operations-status document, and render it three ways: a compact text view
for SSH, an HTML page, and a JSON document for automation (item 123).

Each collector (`scripts/collect_vps_status.py`, `collect_github_status.py`,
`collect_oracle_status.py`, `collect_r2_status.py`) already returns one
`scripts/ops_status.py`-contract `ComponentStatus` for its own component, gathered
independently with its own failure/fallback handling. Nothing before this module
combines them -- that is this module's whole job:

- `collect_all` calls all four, in a fixed order, isolating each behind its own
  `try`/`except`: one collector raising must never take the other three down with
  it, and must never surface as anything other than a synthesized `unknown`
  component document (`_unknown_status`), carrying a short, already-bounded
  `collector_error` detail -- never the raw exception traceback, matching the
  shared contract's own "no raw logs" rule.
- `oracle_crawler`/`r2` additionally treat a replay rejection
  (`OracleStatusReplayed`/`R2StatusReplayed`) as "the same still-valid document
  already on record", not a failure: their own collector modules document this as
  the *expected* outcome between Oracle's periodic heartbeats (e.g. most calls to
  this page between two 30-minute Oracle publishes), so degrading it to `unknown`
  here would make the page flicker to unknown on every ordinary poll.
- `build_document` wraps the four resulting documents with a combined
  `overall_state` (the worst of the four, by the same severity order
  `oracle_cloud/v3/bin/status-snapshot.sh` already uses:
  failed > stale > degraded > unknown > healthy) and a `reasons` map giving a
  short, human-facing explanation for every non-healthy component, mined from
  that component's own already-bounded `details` bag -- never inventing new
  facts, never touching raw logs.
- `render_text`/`render_html` are the two human-facing views; `build_document`'s
  own dict *is* the JSON view (no separate JSON renderer needed).
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from scripts import ops_status
from scripts.collect_github_status import DEFAULT_CACHE_BASENAME as GITHUB_DEFAULT_CACHE_BASENAME
from scripts.collect_github_status import collect_github_status
from scripts.collect_oracle_status import DEFAULT_CACHE_PATH as ORACLE_DEFAULT_CACHE_PATH
from scripts.collect_oracle_status import DEFAULT_REPO as ORACLE_DEFAULT_GITHUB_REPO_SLUG
from scripts.collect_oracle_status import OracleStatusReplayed, collect_oracle_status
from scripts.collect_r2_status import DEFAULT_CACHE_PATH as R2_DEFAULT_CACHE_PATH
from scripts.collect_r2_status import R2StatusReplayed, collect_r2_status
from scripts.collect_vps_status import collect_vps_loop_status

DEFAULT_LOCAL_REPO = Path("/root/transit-app")
DEFAULT_GITHUB_REPO_SLUG = ORACLE_DEFAULT_GITHUB_REPO_SLUG

DOCUMENT_SCHEMA_VERSION = 1

COMPONENT_ORDER: tuple[str, ...] = ("vps_loop", "github", "oracle_crawler", "r2")

# failed > stale > degraded > unknown > healthy -- matches
# oracle_cloud/v3/bin/status-snapshot.sh's own `severity_rank`.
_STATE_SEVERITY: Mapping[str, int] = {"healthy": 0, "degraded": 1, "unknown": 2, "stale": 3, "failed": 4}

# Thresholds are irrelevant to the resulting state here: `last_success_at=None`
# with `reported_failure=False` always classifies as `unknown` regardless of
# their value (see `ops_status.classify_state`); kept symbolic rather than
# arbitrary magic numbers only for readability.
_SYNTHETIC_UNKNOWN_THRESHOLD_SECONDS = 3600.0


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _truncate(text: str, limit: int = ops_status.MAX_DETAIL_STRING_LENGTH) -> str:
    """Collapse whitespace and cap length so a collector's exception message can never
    exceed the shared contract's own per-string bound or smuggle embedded newlines into
    the compact text/CLI views."""

    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _unknown_status(component: str, *, now: datetime, reason: str) -> dict:
    """A synthesized `unknown` document for a component whose collector itself could not
    run. Never fabricates a healthy/degraded/stale/failed verdict, and never carries more
    than a short, truncated summary of the failure -- `validate_details` would reject a
    raw traceback or credential-shaped key outright, but this stays well inside its bounds
    on purpose, not merely by luck of what `validate_details` happens to catch."""

    status = ops_status.build_status(
        component=component,
        observed_at=now,
        last_success_at=None,
        healthy_max_age_seconds=_SYNTHETIC_UNKNOWN_THRESHOLD_SECONDS,
        stale_max_age_seconds=_SYNTHETIC_UNKNOWN_THRESHOLD_SECONDS,
        details={"collector_error": _truncate(reason)},
        now=now,
    )
    return ops_status.to_json_dict(status)


def _collect_vps_loop(*, local_repo: Path, now: datetime) -> dict:
    status = collect_vps_loop_status(repo=local_repo, now=now)
    return ops_status.to_json_dict(status)


def _collect_github(*, local_repo: Path, cache_path: Path, now: datetime) -> dict:
    status = collect_github_status(repo=local_repo, cache_path=cache_path, now=now)
    return ops_status.to_json_dict(status)


def _collect_oracle_crawler(*, github_repo_slug: str, cache_path: Path) -> dict:
    try:
        status = collect_oracle_status(repo=github_repo_slug, cache_path=cache_path)
    except OracleStatusReplayed as exc:
        status = exc.status
    return ops_status.to_json_dict(status)


def _collect_r2(*, github_repo_slug: str, cache_path: Path) -> dict:
    try:
        status = collect_r2_status(repo=github_repo_slug, cache_path=cache_path)
    except R2StatusReplayed as exc:
        status = exc.status
    return ops_status.to_json_dict(status)


def collect_all(
    *,
    local_repo: Path = DEFAULT_LOCAL_REPO,
    github_repo_slug: str = DEFAULT_GITHUB_REPO_SLUG,
    github_cache_path: Path | None = None,
    oracle_cache_path: Path = ORACLE_DEFAULT_CACHE_PATH,
    r2_cache_path: Path = R2_DEFAULT_CACHE_PATH,
    now: datetime | None = None,
) -> list[dict]:
    """Collect all four components' status documents, in `COMPONENT_ORDER`.

    Every collector call is individually isolated: a raised exception (any type --
    a collector's own module may raise an exception class loaded under a different
    identity than this module's own imports, see `scripts/collect_vps_status.py`'s
    sibling-loading rationale, so this deliberately does not rely on `isinstance`)
    degrades only that one component to `unknown` via `_unknown_status`, never the
    other three.
    """

    now = now or datetime.now(timezone.utc)
    github_cache_path = github_cache_path or (local_repo / GITHUB_DEFAULT_CACHE_BASENAME)

    jobs: list[tuple[str, Callable[[], dict]]] = [
        ("vps_loop", lambda: _collect_vps_loop(local_repo=local_repo, now=now)),
        ("github", lambda: _collect_github(local_repo=local_repo, cache_path=github_cache_path, now=now)),
        (
            "oracle_crawler",
            lambda: _collect_oracle_crawler(github_repo_slug=github_repo_slug, cache_path=oracle_cache_path),
        ),
        ("r2", lambda: _collect_r2(github_repo_slug=github_repo_slug, cache_path=r2_cache_path)),
    ]

    documents: list[dict] = []
    for component, job in jobs:
        try:
            documents.append(job())
        except Exception as exc:
            documents.append(_unknown_status(component, now=now, reason=f"{type(exc).__name__}: {exc}"))
    return documents


def overall_state(documents: Sequence[dict]) -> str:
    if not documents:
        return "unknown"
    return max((doc["state"] for doc in documents), key=lambda state: _STATE_SEVERITY.get(state, 0))


def _component_sort_key(document: dict) -> int:
    component = document.get("component")
    return COMPONENT_ORDER.index(component) if component in COMPONENT_ORDER else len(COMPONENT_ORDER)


def build_document(documents: Sequence[dict], *, now: datetime | None = None) -> dict:
    """Wrap already-collected component documents into the combined page/CLI/JSON shape.

    Deliberately does NOT inline `reasons` into each component's own dict: every
    entry in `components` stays a byte-for-byte valid `ops_status.py`-contract
    document (so a consumer can still run `ops_status.validate_document` on any one
    of them directly), and the added human-facing summary lives in the separate
    `reasons` map instead.
    """

    now = now or datetime.now(timezone.utc)
    ordered = sorted(documents, key=_component_sort_key)
    reasons = {doc["component"]: reason for doc in ordered if (reason := reason_for(doc)) is not None}
    return {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "generated_at": _isoformat(now),
        "overall_state": overall_state(ordered),
        "components": ordered,
        "reasons": reasons,
    }


def _generic_reason(state: str, age_seconds: int | None, last_success_at: str | None) -> str:
    if state == "unknown":
        if last_success_at is None:
            return "no successful observation has ever been recorded"
        return "state cannot be determined (clock skew or an internally inconsistent report)"
    if state == "failed":
        return "the component itself reported an explicit failure"
    if state == "stale":
        return (
            f"no successful update in {age_seconds}s, past the staleness threshold"
            if age_seconds is not None
            else ("no successful update on record")
        )
    if state == "degraded":
        return (
            f"running behind: {age_seconds}s since the last success"
            if age_seconds is not None
            else ("running behind schedule")
        )
    return f"state is {state}"


def _vps_loop_reason(details: Mapping[str, object]) -> str | None:
    activity = details.get("loop_activity")
    blocker = details.get("blocker_class")
    if activity == "restarting":
        return f"repeatedly blocked on {blocker or 'the same issue'} without making progress"
    if activity == "paused":
        return f"circuit-breaker paused (blocked on {blocker or 'an unspecified issue'})"
    if details.get("systemd_active_state") == "failed":
        return "claude-loop.service reported a failed run"
    return None


def _github_reason(details: Mapping[str, object]) -> str | None:
    parts: list[str] = []
    if details.get("last_error_kind"):
        parts.append(f"last GitHub check failed ({details['last_error_kind']})")
    conflicting = details.get("conflicting_pr_count") or 0
    if conflicting:
        parts.append(f"{conflicting} PR(s) conflicting")
    failing = details.get("failing_checks_pr_count") or 0
    if failing:
        parts.append(f"{failing} PR(s) with failing checks")
    return "; ".join(parts) or None


def _oracle_crawler_reason(details: Mapping[str, object]) -> str | None:
    parts: list[str] = []
    for label, key in (("RT feed", "rt_state"), ("static feed", "static_state"), ("R2 sync/verify", "r2_state")):
        value = details.get(key)
        if value not in (None, "healthy", "not_applicable"):
            parts.append(f"{label} is {value}")
    return "; ".join(parts) or None


def _r2_reason(details: Mapping[str, object]) -> str | None:
    parts: list[str] = []
    if details.get("disk_state") not in (None, "healthy"):
        parts.append(f"disk usage is {details.get('disk_used_pct')}% ({details.get('disk_state')})")
    if details.get("r2_state") not in (None, "healthy", "not_applicable"):
        parts.append(f"R2 listing is {details.get('r2_state')} ({details.get('r2_listing_result')})")
    return "; ".join(parts) or None


_SPECIFIC_REASON_BUILDERS: Mapping[str, Callable[[Mapping[str, object]], str | None]] = {
    "vps_loop": _vps_loop_reason,
    "github": _github_reason,
    "oracle_crawler": _oracle_crawler_reason,
    "r2": _r2_reason,
}


def reason_for(document: dict) -> str | None:
    """A short, human-facing explanation for a non-healthy component, or `None` for a
    healthy one. Prefers a component-specific reason mined from its own `details`
    (e.g. which PRs are conflicting, which feed is stale); falls back to a generic
    state-based explanation when no specific detail applies."""

    state = document["state"]
    if state == "healthy":
        return None

    details = document.get("details") or {}
    collector_error = details.get("collector_error")
    if collector_error:
        return _truncate(f"collector could not run: {collector_error}")

    builder = _SPECIFIC_REASON_BUILDERS.get(document.get("component", ""))
    specific = builder(details) if builder is not None else None
    if specific:
        return _truncate(specific)

    return _truncate(_generic_reason(state, document.get("age_seconds"), document.get("last_success_at")))


def _vps_loop_highlight(details: Mapping[str, object]) -> str:
    current_item = details.get("current_item")
    activity = details.get("loop_activity")
    return f"current_item={current_item if current_item is not None else 'none'} activity={activity or 'unknown'}"


def _github_highlight(details: Mapping[str, object]) -> str:
    return (
        f"open_prs={details.get('open_pr_count', '?')} "
        f"draft={details.get('draft_pr_count', '?')} "
        f"conflicting={details.get('conflicting_pr_count', '?')} "
        f"failing_checks={details.get('failing_checks_pr_count', '?')}"
    )


def _oracle_crawler_highlight(details: Mapping[str, object]) -> str:
    return (
        f"rt={details.get('rt_state', 'unknown')} "
        f"static={details.get('static_state', 'unknown')} "
        f"r2_sync={details.get('r2_state', 'unknown')}"
    )


def _r2_highlight(details: Mapping[str, object]) -> str:
    return (
        f"disk={details.get('disk_used_pct', '?')}% "
        f"rt_bytes={details.get('rt_bytes', '?')} "
        f"static_bytes={details.get('static_bytes', '?')} "
        f"total_bytes={details.get('r2_bytes_total', '?')}"
    )


_HIGHLIGHT_BUILDERS: Mapping[str, Callable[[Mapping[str, object]], str]] = {
    "vps_loop": _vps_loop_highlight,
    "github": _github_highlight,
    "oracle_crawler": _oracle_crawler_highlight,
    "r2": _r2_highlight,
}


def highlight_for(document: dict) -> str:
    """A short, always-present (healthy or not) summary of the facts item 123 calls out
    by name: current task, CI/PR summary, crawler freshness, disk/R2 usage."""

    builder = _HIGHLIGHT_BUILDERS.get(document.get("component", ""))
    if builder is None:
        return ""
    try:
        return builder(document.get("details") or {})
    except Exception:
        return ""


def render_text(document: dict) -> str:
    """The compact text view for SSH: one summary line, then one block per component."""

    lines = [f"ops-status  generated_at={document['generated_at']}  overall={document['overall_state']}"]
    for component in document["components"]:
        age = component["age_seconds"]
        age_str = f"{age}s" if age is not None else "n/a"
        last_success = component["last_success_at"] or "never"
        lines.append(
            f"- {component['component']:<14} state={component['state']:<8} age={age_str:<8} last_success={last_success}"
        )
        highlight = highlight_for(component)
        if highlight:
            lines.append(f"    {highlight}")
        reason = document["reasons"].get(component["component"])
        if reason:
            lines.append(f"    reason: {reason}")
    return "\n".join(lines) + "\n"


_HTML_STATE_CLASSES = frozenset({"healthy", "degraded", "stale", "failed", "unknown"})


def render_html(document: dict) -> str:
    """A small, dependency-free HTML page (no Jinja2/templating elsewhere in this repo to
    reuse). Every value is `html.escape`d before interpolation -- `details` values are
    caller-controlled facts relayed from GitHub/Oracle, never treated as trusted markup."""

    rows = []
    for component in document["components"]:
        state = component["state"]
        state_class = state if state in _HTML_STATE_CLASSES else "unknown"
        age = component["age_seconds"]
        reason = document["reasons"].get(component["component"]) or "—"
        rows.append(
            "<tr>"
            f"<td>{html.escape(component['component'])}</td>"
            f'<td class="state-{html.escape(state_class)}">{html.escape(state)}</td>'
            f"<td>{age if age is not None else '—'}</td>"
            f"<td>{html.escape(component['last_success_at'] or 'never')}</td>"
            f"<td>{html.escape(highlight_for(component) or '—')}</td>"
            f"<td>{html.escape(reason)}</td>"
            "</tr>"
        )

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Operations status</title>\n"
        "<style>\n"
        "body { font-family: system-ui, sans-serif; margin: 2rem; color: #1c1e21; background: #fafafa; }\n"
        "table { border-collapse: collapse; width: 100%; }\n"
        "th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; }\n"
        ".state-healthy { color: #1a7f37; }\n"
        ".state-degraded, .state-stale { color: #9a6700; }\n"
        ".state-failed { color: #b3261e; }\n"
        ".state-unknown { color: #57606a; }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Operations status</h1>\n"
        f"<p>generated_at: {html.escape(document['generated_at'])} &mdash; "
        f'overall: <strong class="state-{html.escape(document["overall_state"])}">'
        f"{html.escape(document['overall_state'])}</strong></p>\n"
        "<table>\n"
        "<thead><tr><th>Component</th><th>State</th><th>Age (s)</th><th>Last success</th>"
        "<th>Summary</th><th>Reason</th></tr></thead>\n"
        f"<tbody>{''.join(rows)}</tbody>\n"
        "</table>\n"
        "</body>\n"
        "</html>\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point -- the "compact text view for SSH": prints the combined
    operations-status document as text by default, or `--format json` for automation.

    Exit code: 0 when the combined `overall_state` is `healthy`/`degraded`, 1 otherwise
    (mirrors every sibling `collect_*_status.py` script's own convention).
    """

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo", type=Path, default=DEFAULT_LOCAL_REPO, help="Local checkout for vps_loop/github facts"
    )
    parser.add_argument(
        "--github-repo",
        default=DEFAULT_GITHUB_REPO_SLUG,
        help="owner/repo slug for the GitHub-relayed oracle_crawler/r2 heartbeat channels",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--out", type=Path, default=None, help="Also write the JSON document to this path")
    args = parser.parse_args(argv)

    documents = collect_all(local_repo=args.repo.resolve(), github_repo_slug=args.github_repo)
    document = build_document(documents)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    if args.format == "json":
        print(json.dumps(document, indent=2))
    else:
        sys.stdout.write(render_text(document))

    return 0 if document["overall_state"] in ("healthy", "degraded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
