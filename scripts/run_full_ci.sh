#!/usr/bin/env bash
# Reproducible "full CI" run for one VPS worktree/job: builds and starts a
# dedicated, uniquely-named/-ported Postgres + ClickHouse pair, applies
# schema, runs the same lint/type/test gate as
# .github/workflows/ci.yml's `test` job, then always tears both
# containers down again -- success or failure.
#
# Why this exists: transit-app-gotchas's documented manual recipe
# (`docker run --name transit-test-pg -p 5544:5432 ...` / `make ch-test`'s
# `transit-test-ch` on :8124) names both the container and the host port
# literally, and this repo also keeps a long-lived pair of containers by
# those exact names running for everyday local use. Two verification runs
# against that same fixed pair -- e.g. an interactive session's own
# verification and a concurrent `/vps-loop-run` worker's, in two different
# worktrees on the same VPS -- don't just risk a `docker run` name
# collision: `tests/conftest.py`'s per-test Postgres `TRUNCATE ... CASCADE`
# and its ClickHouse `DROP TABLE`/`CREATE TABLE` both race across the two
# runs, producing spurious failures with no connection to either diff (see
# docs/refactor-log.md's item 104 entry for a real occurrence). This script
# picks a fresh container name and a free host port on every invocation
# instead, so any number of worktrees/jobs can run it at the same time on
# one host without coordinating -- it never touches the shared
# `transit-test-pg`/`transit-test-ch` containers at all.
#
# Usage: scripts/run_full_ci.sh [extra pytest args...]
# Requires: docker, poetry (with `poetry install` already run in this
# worktree's own virtualenv -- this script does not install dependencies).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Unique per invocation, not just per worktree: two runs started back to
# back in the same worktree (e.g. a re-run before a previous run's teardown
# trap has fully finished) must not collide either. Sanitized to Docker's
# container-name charset (`[a-zA-Z0-9][a-zA-Z0-9_.-]*`).
raw_id="$(basename "$(pwd)")-$$"
instance_id="$(printf '%s' "$raw_id" | tr -c 'A-Za-z0-9_.-' '-')"
pg_name="transit-fullci-pg-${instance_id}"
ch_name="transit-fullci-ch-${instance_id}"

cleanup() {
  docker rm -f "$pg_name" "$ch_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Ask the OS for two currently-free ports rather than probing/reusing a
# fixed pair: a fixed pair reintroduces the exact collision this script
# exists to avoid. There is an unavoidable time-of-check-to-time-of-use
# gap between this and `docker run -p` below (freeing the socket here so
# Docker can bind it there) -- closed by start_containers's retry loop
# rather than assumed away.
free_port_pair() {
  python3 - <<'PY'
import socket
socks = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(2)]
for s in socks:
    s.bind(("127.0.0.1", 0))
for s in socks:
    print(s.getsockname()[1])
for s in socks:
    s.close()
PY
}

pg_port=""
ch_port=""

start_containers() {
  local attempt ports
  for attempt in 1 2 3 4 5; do
    ports="$(free_port_pair)"
    pg_port="$(sed -n 1p <<<"$ports")"
    ch_port="$(sed -n 2p <<<"$ports")"

    if docker run -d --name "$pg_name" \
        -e POSTGRES_USER=transit -e POSTGRES_PASSWORD=transit -e POSTGRES_DB=transit_test \
        -p "127.0.0.1:${pg_port}:5432" "$pg_image" >/dev/null 2>&1 \
      && docker run -d --name "$ch_name" \
        -e CLICKHOUSE_USER=transit -e CLICKHOUSE_PASSWORD=transit -e CLICKHOUSE_DB=transit_test \
        -p "127.0.0.1:${ch_port}:8123" clickhouse/clickhouse-server:26.3 >/dev/null 2>&1
    then
      return 0
    fi
    docker rm -f "$pg_name" "$ch_name" >/dev/null 2>&1 || true
    sleep 1
  done
  echo "run_full_ci.sh: could not start isolated containers after 5 attempts" >&2
  exit 1
}

echo "→ building db/ image (PostGIS + pgvector, same image every job uses)"
pg_image="$(docker build -q db/)"

echo "→ starting isolated Postgres + ClickHouse (${pg_name}, ${ch_name})"
start_containers

# `-h localhost` forces the check onto TCP, matching
# .github/actions/start-test-postgres's own reasoning: the official image's
# first run answers on the Unix socket from a bootstrap server before the
# real, TCP-listening server replaces it.
echo "→ waiting for Postgres readiness"
pg_ready=0
for _ in $(seq 1 60); do
  if docker exec "$pg_name" pg_isready -h localhost -U transit -d transit_test >/dev/null 2>&1; then
    pg_ready=1
    break
  fi
  sleep 2
done
if [ "$pg_ready" != "1" ]; then
  echo "run_full_ci.sh: Postgres did not become ready" >&2
  docker logs "$pg_name" >&2 || true
  exit 1
fi

echo "→ waiting for ClickHouse readiness"
ch_ready=0
for _ in $(seq 1 60); do
  if docker exec "$ch_name" wget --spider -q http://localhost:8123/ping >/dev/null 2>&1; then
    ch_ready=1
    break
  fi
  sleep 2
done
if [ "$ch_ready" != "1" ]; then
  echo "run_full_ci.sh: ClickHouse did not become ready" >&2
  docker logs "$ch_name" >&2 || true
  exit 1
fi

export DATABASE_URL="postgresql://transit:transit@localhost:${pg_port}/transit_test"
export CLICKHOUSE_HOST=localhost
export CLICKHOUSE_PORT="${ch_port}"
export CLICKHOUSE_USER=transit
export CLICKHOUSE_PASSWORD=transit
export CLICKHOUSE_DATABASE=transit_test
export GROQ_API_KEY="${GROQ_API_KEY:-test-key}"

echo "→ applying Postgres schema"
poetry run python gtfs_pipeline.py migrate up

echo "→ applying ClickHouse schema"
poetry run python -c "from pipeline.clickhouse import get_client; \
from db.clickhouse.bootstrap import apply_schema; apply_schema(get_client())"

echo "→ lint"
poetry run ruff check .
poetry run ruff format --check .

echo "→ type check"
poetry run mypy

echo "→ tests"
TEST_PG_PORT="$pg_port" TEST_CH_PORT="$ch_port" \
  scripts/run_integration_tests.sh --cov=api --cov=pipeline --cov=db --cov-report=term "$@"
