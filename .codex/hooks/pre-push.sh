#!/usr/bin/env bash
set -euo pipefail

# Runs before every Bash call, so skip the git lookups unless the raw input
# could hold a push; the wrapped gate makes the real decision.
input="$(cat)"
case "$input" in
  *push*) ;;
  *) exit 0 ;;
esac

# The common git directory sits in the main checkout whichever branch that
# checkout holds, so the gate always borrows the shared environment from there.
common_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
CLAUDE_PROJECT_DIR="$(dirname "$common_dir")"
export CLAUDE_PROJECT_DIR
repo_root="$(git rev-parse --show-toplevel)"
exec bash "$repo_root/.claude/hooks/guard-push-quality.sh" <<<"$input"
