#!/usr/bin/env bash
# Runs `poetry run pytest` against the throwaway Postgres (:5544)/ClickHouse
# (:8124) test stack, with the required env block already set inside this
# script instead of prepended on the command line.
#
# Why this script exists: a Bash permission allowlist entry like
# `Bash(poetry run pytest*)` only matches a command whose literal text
# starts with "poetry" -- prepending `DATABASE_URL=... RUN_CH_INTEGRATION=1
# ... poetry run pytest` breaks that match outright, since the command now
# starts with "DATABASE_URL=". That is exactly what blocked items 16, 21,
# 22, 23, and 25's own DB-backed verification from running unattended in a
# sandboxed VPS-loop worker session, each one worked around by hand instead
# of fixed at the root -- see transit-app-gotchas's "VPS loop / sandboxed
# worker sessions" section. Allowlisting this script's own fixed prefix
# (`Bash(scripts/run_integration_tests.sh*)`) closes the gap for good.
#
# Usage: scripts/run_integration_tests.sh [--llm-eval] [--dashboard-e2e] <pytest args...>
#   --llm-eval       sets RUN_LLM_EVAL=1. Needs a real GROQ_API_KEY already
#                    exported in the environment -- this script does NOT
#                    fabricate one, since a fake key would make a live-LLM
#                    test fail confusingly (a bad-auth error) instead of
#                    clearly (the app's own "GROQ_API_KEY env var is
#                    required" message).
#   --dashboard-e2e  sets RUN_DASHBOARD_E2E_SCAN=1 and, only if GROQ_API_KEY
#                    isn't already set, a placeholder value -- the dashboard
#                    e2e test boots the full app (whose startup unconditionally
#                    requires a key) but never reaches the Ask/LLM code path.
set -euo pipefail

export DATABASE_URL="${DATABASE_URL:-postgresql://transit:transit@localhost:5544/transit_test}"
export RUN_CH_INTEGRATION=1
export CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-localhost}"
export CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8124}"
export CLICKHOUSE_USER="${CLICKHOUSE_USER:-transit}"
export CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-transit}"
export CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-transit_test}"

pytest_args=()
llm_eval=0
for arg in "$@"; do
  case "$arg" in
    --llm-eval)
      llm_eval=1
      export RUN_LLM_EVAL=1
      ;;
    --dashboard-e2e)
      export RUN_DASHBOARD_E2E_SCAN=1
      export GROQ_API_KEY="${GROQ_API_KEY:-dummy-not-used-by-this-test}"
      ;;
    *)
      pytest_args+=("$arg")
      ;;
  esac
done

# Fail fast with a clear message rather than letting the live-LLM call
# itself fail confusingly ("no usable providers") mid-test.
if [ "$llm_eval" = "1" ] && [ -z "${GROQ_API_KEY:-}" ]; then
  echo "run_integration_tests.sh: --llm-eval requires a real GROQ_API_KEY" \
    "already exported in the environment (this script does not fabricate" \
    "one)." >&2
  exit 1
fi

# `${pytest_args[@]+"${pytest_args[@]}"}`, not the plain `"${pytest_args[@]}"`
# expansion: under `set -u`, bash < 4.4 (e.g. macOS's default bash 3.2)
# treats an EMPTY array's `[@]` expansion as an unbound-variable error,
# which would crash this exact script on its own plain "run everything, no
# extra pytest args" invocation -- the case most likely to end up in a
# permission allowlist and run unattended.
exec poetry run pytest ${pytest_args[@]+"${pytest_args[@]}"}
