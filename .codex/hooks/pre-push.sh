#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
main_root="$(git -C "$repo_root" worktree list --porcelain | awk '
  /^worktree / { dir = substr($0, 10) }
  /^branch refs\/heads\/main$/ { print dir; exit }
')"
export CLAUDE_PROJECT_DIR="${main_root:-$repo_root}"
exec bash "$repo_root/.claude/hooks/guard-push-quality.sh"
