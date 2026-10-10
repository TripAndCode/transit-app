#!/usr/bin/env bash
# PreToolUse(Bash) hook: block write/DDL SQL aimed at a dev store (the ports in
# guard_dev_db.py's DEV_PORTS), teardown of a dev volume, and a destructive
# Make target or gtfs_pipeline subcommand not pointed at the throwaway stack.
# Thin wrapper so the hook stays at the path settings.json registers; the
# logic (and its tests) live in guard_dev_db.py next to it.
set -euo pipefail
exec python3 "$(dirname "$0")/guard_dev_db.py"
