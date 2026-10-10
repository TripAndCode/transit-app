#!/usr/bin/env bash
# Prints the Postgres connection to add to Dekart, derived from DATABASE_URL
# (the environment, else the repo's `.env`) rather than a fixed port: the dev
# instance can be published on a different port than compose.yml declares.
# The URL is rewritten to reach the host from inside the container and carries
# `default_transaction_read_only=on`, so Dekart's sessions refuse writes. The
# password is masked in the output; enter it from the same DATABASE_URL.
set -euo pipefail

command -v curl >/dev/null || { echo "ERROR: curl not installed."; exit 1; }

url="${DATABASE_URL:-}"
env_file="$(cd "$(dirname "$0")/../.." && pwd)/.env"
if [ -z "$url" ] && [ -f "$env_file" ]; then
  url="$(grep -E '^DATABASE_URL=' "$env_file" | tail -n 1 | cut -d= -f2-)"
fi
[ -n "$url" ] || { echo "ERROR: DATABASE_URL is not set (environment or .env)."; exit 1; }

scheme="${url%%://*}"
rest="${url#*://}"
creds="${rest%%@*}"
target="${rest#*@}"
[ "$creds" != "$rest" ] || { echo "ERROR: DATABASE_URL has no user@host part."; exit 1; }
user="${creds%%:*}"
hostport="${target%%/*}"
path="${target#*/}"
host="${hostport%%:*}"
port=5432
case "$hostport" in *:*) port="${hostport##*:}" ;; esac
case "$host" in localhost | 127.0.0.1 | ::1) host=host.docker.internal ;; esac
sep='?'
case "$path" in *\?*) sep='&' ;; esac
shown="${scheme}://${user}:***@${host}:${port}/${path}${sep}options=-c%20default_transaction_read_only%3Don"

echo "→ waiting for Dekart..."
deadline=$((SECONDS + ${DEKART_WAIT_SECONDS:-60}))
until curl -sf http://localhost:8080 >/dev/null 2>&1; do
  [ "$SECONDS" -lt "$deadline" ] || { echo "ERROR: Dekart did not answer on http://localhost:8080."; exit 1; }
  sleep 1
done

echo ""
echo "✓ Dekart ready. Open http://localhost:8080 and add this connection:"
echo ""
echo "  Postgres (read-only session on real dev data):"
echo "    $shown"
echo ""
echo "Replace *** with the password from DATABASE_URL."
echo "WARNING: this connection points at REAL dev data. Never run write/DDL"
echo "queries through GeoSQL/Dekart against it -- read-only only, per AGENTS.md."
