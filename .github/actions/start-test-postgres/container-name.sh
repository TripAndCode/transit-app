#!/usr/bin/env bash
# Derive the test-Postgres container name for one CI run.
#
# The name is scoped to exactly the unit ci.yml's `concurrency` group
# serializes: the pull request number when there is one, the ref otherwise.
# That equivalence is the whole point. Runs sharing a group can never overlap,
# so they can safely share a container — and a run cancelled before its
# teardown is reclaimed by the next run of the same pull request, which a
# per-run name could never do. Runs in different groups can overlap, and get
# different containers, which is what lets two of them share a Docker daemon.
#
# Cancellation is the common case, not the rare one: the group sets
# `cancel-in-progress` for pull requests, so every re-push abandons a
# container mid-life.
#
# Docker accepts [a-zA-Z0-9][a-zA-Z0-9_.-]* and a ref like
# `refs/heads/fix/a-b` does not qualify, so everything else collapses to a
# dash.
#
# Usage: container-name.sh <prefix> <scope>
set -euo pipefail

prefix="${1:?usage: container-name.sh <prefix> <scope>}"
scope="${2-}"

clean="$(printf '%s' "$scope" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_.-' '-')"
clean="$(printf '%s' "$clean" | sed -E 's/-+/-/g; s/^[-._]+//; s/-+$//')"

# A long branch name would push the name past anything readable in
# `docker ps`. The tail is the part that distinguishes one ref from another.
if [ "${#clean}" -gt 40 ]; then
  clean="${clean: -40}"
  clean="${clean#-}"
fi

# An empty scope would yield a trailing dash and, worse, one shared name for
# every caller that hit this path.
[ -n "$clean" ] || clean="unscoped"

printf '%s-%s\n' "$prefix" "$clean"
