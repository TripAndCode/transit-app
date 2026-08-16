#!/usr/bin/env bash
set -euo pipefail

# Check for required tools
command -v aws >/dev/null || { echo "ERROR: aws CLI not installed. Install via 'brew install awscli' or 'pip install awscli'."; exit 1; }
command -v curl >/dev/null || { echo "ERROR: curl not installed."; exit 1; }

# Set default AWS credentials for LocalStack (LocalStack accepts any values; these are dummy defaults)
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_REGION="${AWS_REGION:-us-east-1}"

ENDPOINT="http://localhost:4566"
BUCKET="dekart-geosql-local"

echo "→ waiting for LocalStack..."
until curl -sf "$ENDPOINT/_localstack/health" >/dev/null 2>&1; do sleep 1; done

echo "→ creating bucket s3://$BUCKET"
AWS_OUTPUT=$(aws --endpoint-url="$ENDPOINT" s3api create-bucket --bucket "$BUCKET" 2>&1) || {
  EXIT_CODE=$?
  # Check if error is specifically about bucket already existing
  if echo "$AWS_OUTPUT" | grep -qi "BucketAlreadyExists\|BucketAlreadyOwnedByYou"; then
    echo "  (bucket already exists, continuing)"
  else
    echo "ERROR: Failed to create bucket. Output: $AWS_OUTPUT" >&2
    exit $EXIT_CODE
  fi
}

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
