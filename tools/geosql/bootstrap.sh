#!/usr/bin/env bash
set -euo pipefail

command -v curl >/dev/null || { echo "ERROR: curl not installed."; exit 1; }

echo "→ waiting for Dekart..."
until curl -sf http://localhost:8080 >/dev/null 2>&1; do sleep 1; done

echo ""
echo "✓ Dekart ready. Open http://localhost:8080 and add this connection:"
echo ""
echo "  Postgres (read-only dev data):"
echo "    postgresql://transit:transit@host.docker.internal:5433/transit"
echo ""
echo "WARNING: this connection points at REAL dev data. Never run write/DDL"
echo "queries through GeoSQL/Dekart against it -- read-only only, per CLAUDE.md."
