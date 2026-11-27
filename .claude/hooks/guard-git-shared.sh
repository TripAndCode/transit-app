#!/usr/bin/env bash
# PreToolUse(Bash) hook: refuse a push that lands on main, a force-push without
# --force-with-lease, and git stash pop/drop/clear -- the shared-clone rules in
# AGENTS.md. Thin wrapper so the hook stays at the path settings.json registers;
# the logic (and its tests) live in guard_git_shared.py next to it.
set -euo pipefail
exec python3 "$(dirname "$0")/guard_git_shared.py"
