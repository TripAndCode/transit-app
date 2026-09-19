#!/usr/bin/env bash
# PreToolUse(Bash) hook: block write/DDL SQL or ClickHouse writes aimed at the
# dev Postgres (:5433, container transit-pg) or dev ClickHouse
# (transit-ch, :8123) -- both hold real production data and are read-only
# for agents (see CLAUDE.md). Reads the tool input JSON on stdin; exit 2 =
# block the tool call. Fails open: any error reading/parsing that JSON, or
# simply finding no destructive-command match, falls through to exit 0
# rather than blocking a Bash call this hook couldn't confidently classify.
set -euo pipefail
input="$(cat)"
cmd="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"

# Postgres write/DDL: SQL keywords, the dropdb/createdb/pg_restore CLIs, a
# `psql ... -f <file>` script run, a `\copy ... FROM` data load (the read
# direction, `\copy ... TO`, is deliberately not matched), migrate-down/
# downgrade, and VACUUM FULL specifically -- plain VACUUM takes no exclusive
# lock and mutates no rows, so it's left unmatched.
pg_write_re='\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|REINDEX)\b'
pg_write_re="${pg_write_re}"'|\b(dropdb|createdb|pg_restore)\b'
pg_write_re="${pg_write_re}"'|db-reset|migrate[^ ]*down|downgrade'
pg_write_re="${pg_write_re}"'|psql\b.*[[:space:]]-f\b'
pg_write_re="${pg_write_re}"'|\\copy[^|&;]*\bfrom\b'
pg_write_re="${pg_write_re}"'|VACUUM[[:space:]]+FULL'

# Only care about commands that touch the dev DB port/host/container.
if printf '%s' "$cmd" | grep -Eqi 'localhost:5433|@[^ ]*:5433|transit-pg'; then
  if printf '%s' "$cmd" | grep -Eqi "$pg_write_re"; then
    echo "BLOCKED: write/DDL SQL against dev DB :5433 (read-only, real production data). Use the :5544 test DB. See CLAUDE.md." >&2
    exit 2
  fi
fi

# ClickHouse write/DDL: a destructive keyword issued via clickhouse-client
# or a raw HTTP query (curl) against the dev host/port.
if printf '%s' "$cmd" | grep -Eqi 'transit-ch|:8123'; then
  if printf '%s' "$cmd" | grep -Eqi '\b(clickhouse-client|curl)\b' \
    && printf '%s' "$cmd" | grep -Eqi '\b(INSERT|ALTER|DROP|TRUNCATE)\b'; then
    echo "BLOCKED: write/DDL against dev ClickHouse (transit-ch :8123, read-only, real production data). Use the :8124 test ClickHouse. See CLAUDE.md." >&2
    exit 2
  fi
fi

exit 0
