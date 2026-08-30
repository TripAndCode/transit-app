#!/usr/bin/env bash
# PreToolUse(Bash) hook: block write/DDL SQL aimed at the dev DB on :5433.
# Reads the tool input JSON on stdin; exit 2 = block the tool call.
set -euo pipefail
input="$(cat)"
cmd="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"

# Only care about commands that touch the dev DB port/host/container.
if printf '%s' "$cmd" | grep -Eqi 'localhost:5433|@[^ ]*:5433|transit-pg'; then
  if printf '%s' "$cmd" | grep -Eqi '\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b|db-reset|migrate[^ ]*down|downgrade'; then
    echo "BLOCKED: write/DDL SQL against dev DB :5433 (read-only, real production data). Use the :5544 test DB. See CLAUDE.md." >&2
    exit 2
  fi
fi
exit 0
