#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="http://localhost:4566"
BUCKET="dekart-geosql-local"

echo "→ waiting for LocalStack..."
until curl -sf "$ENDPOINT/_localstack/health" >/dev/null 2>&1; do sleep 1; done

echo "→ creating bucket s3://$BUCKET"
aws --endpoint-url="$ENDPOINT" s3api create-bucket --bucket "$BUCKET" >/dev/null 2>&1 \
  || echo "  (bucket already exists, continuing)"

echo ""
echo "✓ LocalStack ready. Open http://localhost:8080 and add these connections:"
echo ""
echo "  Postgres (read-only dev data):"
echo "    postgresql://transit:transit@host.docker.internal:5433/transit"
echo ""
echo "  ClickHouse (read-only dev data):"
echo "    http://transit:transit@host.docker.internal:8123/transit"
echo ""
echo "WARNING: both connections point at REAL dev data. Never run write/DDL"
echo "queries through GeoSQL/Dekart against either -- read-only only, per CLAUDE.md."
