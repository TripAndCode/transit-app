#!/usr/bin/env bash
# PreToolUse(Bash) hook: block write/DDL SQL aimed at the dev DB on :5433.
# Thin wrapper so the hook stays at the path settings.json registers; the
# logic (and its tests) live in guard_dev_db.py next to it.
set -euo pipefail
exec python3 "$(dirname "$0")/guard_dev_db.py"
