#!/usr/bin/env python3
"""Post-install/post-rollback smoke check for the operations-monitoring hub.

`scripts/ops_status_page.collect_all` already isolates each of the four
collectors (`vps_loop`, `github`, `oracle_crawler`, `r2`) behind its own
`try`/`except`, so a single broken collector never crashes the combined page
or `ops_alerts.py`'s poll -- it just degrades that one component to
`unknown` with a `collector_error` detail. That isolation is exactly why a
fresh install can look deceptively fine: the status page and the alerting
timer both keep running quietly even if, say, the GitHub collector can never
actually authenticate. This script exists to catch that class of problem
right after a deploy or a rollback, before the next real incident depends on
a collector nobody has actually confirmed works on this box.

A "smoke check pass" here is deliberately not the same thing as "everything
is healthy": right after a fresh install, before any real GTFS/CI/Oracle
activity has accumulated, `unknown` is an entirely expected state for one or
more components. What must never happen is a collector raising in a way
`collect_all` cannot even isolate, returning the wrong set of components, or
one of the returned documents itself failing the shared
`ops_status.validate_document` contract -- each of those means the wiring
itself (not the underlying system being monitored) is broken. A
`collector_error` detail is reported as a warning, not a failure, since it
usually just means a credential or cache path still needs to be configured
on this particular box -- but it is always printed, never swallowed, so an
operator running this right after install sees it immediately.

Exit code: 0 when all four expected components were returned and every one
validates against the shared contract; 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from scripts import ops_status
from scripts.ops_status_page import DEFAULT_GITHUB_REPO_SLUG, DEFAULT_LOCAL_REPO, collect_all


def check_documents(documents: Sequence[dict]) -> list[str]:
    """Return a list of human-readable problems found in `documents` (empty means the
    smoke check passes). Never raises: a malformed document is itself a problem to
    report, not something that should crash this check."""

    problems: list[str] = []

    seen: list[str] = [doc.get("component") or "<unknown>" for doc in documents]
    missing = sorted(ops_status.COMPONENTS - set(seen))
    if missing:
        problems.append(f"missing component(s) in the collected output: {', '.join(missing)}")
    unexpected = sorted(set(seen) - ops_status.COMPONENTS)
    if unexpected:
        problems.append(f"unexpected component(s) in the collected output: {', '.join(unexpected)}")

    for document in documents:
        component = document.get("component", "<unknown>")
        try:
            ops_status.validate_document(document)
        except ops_status.OpsStatusError as exc:
            problems.append(f"{component}: failed contract validation: {exc}")

    return problems


def check_collector_warnings(documents: Sequence[dict]) -> list[str]:
    """Non-fatal warnings: a component collected cleanly (contract-valid) but reported
    a `collector_error`, meaning the collector itself could not run on this box --
    most often a missing credential or cache path right after a fresh install."""

    warnings: list[str] = []
    for document in documents:
        details = document.get("details") or {}
        collector_error = details.get("collector_error")
        if collector_error:
            warnings.append(f"{document.get('component', '<unknown>')}: collector_error: {collector_error}")
    return warnings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo", type=Path, default=DEFAULT_LOCAL_REPO, help="Local checkout for vps_loop/github facts"
    )
    parser.add_argument(
        "--github-repo",
        default=DEFAULT_GITHUB_REPO_SLUG,
        help="owner/repo slug for the GitHub-relayed oracle_crawler/r2 heartbeat channels",
    )
    args = parser.parse_args(argv)

    documents = collect_all(local_repo=args.repo.resolve(), github_repo_slug=args.github_repo)

    problems = check_documents(documents)
    warnings = check_collector_warnings(documents)

    components_seen = sorted(doc.get("component", "<unknown>") for doc in documents)
    print(f"ops-smoke-check  components={','.join(components_seen)}")

    for warning in warnings:
        print(f"WARN: {warning}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    print("OK: all four components collected and validate against the operations-status contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
