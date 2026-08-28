#!/usr/bin/env bash
# PreToolUse(Bash) hook: gate `git push` on lint/test passing first.
# Lint/format checks are scoped to files changed vs `main` (the repo has
# pre-existing lint/format debt elsewhere, so a whole-repo gate would block
# every push); tests and frontend checks run whole-project since those can't
# be meaningfully file-scoped. Fails CLOSED: anywhere this script can't
# determine what changed or can't run a required check, it blocks (exit 2)
# rather than silently letting the push through — set PUSH_GATE_SKIP_TESTS=1
# for a deliberate, visible opt-out of the DB-dependent backend tests only,
# or PUSH_GATE_SKIP_BUILD=1 to skip the frontend build:bundle + entry-chunk
# check specifically.
# Reads the tool input JSON on stdin; exit 2 = block the tool call.
set -uo pipefail
input="$(cat)"

# Cheap pre-filter on the raw JSON before paying for a python3 spawn — the
# command text is embedded verbatim, so a raw substring match is a safe
# superset of the real check below (only skips the python3 spawn, never
# skips the actual gate).
printf '%s' "$input" | grep -q 'push' || exit 0

cmd="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)"
# Anchored to "git" followed by zero or more flag tokens (short -x [value] or
# long --flag[=value]) and then "push" as the next word — matches `git push`,
# `git -C dir push`, `git -c x=y push`, `FOO=bar git push`, but not a bare
# substring hit like `git commit -m "fix push gate"` or `git log --grep=push`,
# where "push" isn't actually the git subcommand. Under-matching (missing a
# real push) is worse than over-matching here, so this stays deliberately
# permissive about flag shapes.
printf '%s' "$cmd" | grep -Eq '\bgit\b(\s+-[A-Za-z](\s+\S+)?|\s+--[A-Za-z][A-Za-z-]*(=\S+)?)*\s+push\b' || exit 0

if [ -z "${CLAUDE_PROJECT_DIR:-}" ]; then
  echo "BLOCKED: git push — CLAUDE_PROJECT_DIR is unset, guard-push-quality.sh cannot locate the repo to run checks." >&2
  exit 2
fi
cd "$CLAUDE_PROJECT_DIR" || { echo "BLOCKED: git push — could not cd to \$CLAUDE_PROJECT_DIR ($CLAUDE_PROJECT_DIR)." >&2; exit 2; }

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
FAIL=0

run_with_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
    return $?
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
    return $?
  fi

  # Neither GNU timeout nor gtimeout is on PATH (e.g. macOS without Homebrew
  # coreutils). Running unbounded here would silently defeat this script's
  # documented fail-closed contract, so enforce the budget with a background
  # watchdog instead. Every real call site is a compound command (bash -c
  # "... && ..."), so job control (`set -m`) is required to put it in its
  # own process group — otherwise TERM/KILL only hits the wrapper shell and
  # leaves its child process tree (npm/vite/pytest workers) running as an
  # orphan.
  local marker; marker="$(mktemp)"
  rm -f "$marker"
  local had_job_control=0
  case $- in *m*) had_job_control=1 ;; esac
  set -m
  "$@" &
  local cmd_pid=$!
  ( sleep "$secs"; : > "$marker"; kill -TERM -"$cmd_pid" 2>/dev/null; sleep 2; kill -KILL -"$cmd_pid" 2>/dev/null ) &
  local watchdog_pid=$!
  wait "$cmd_pid"
  local status=$?
  [ "$had_job_control" -eq 1 ] || set +m
  kill "$watchdog_pid" 2>/dev/null
  wait "$watchdog_pid" 2>/dev/null
  if [ -e "$marker" ]; then
    # Watchdog fired: report GNU timeout's sentinel (124) so call sites'
    # existing `-eq 124` checks keep working, rather than the SIGTERM exit
    # status (143) the fallback path would otherwise produce.
    rm -f "$marker"
    status=124
  fi
  return "$status"
}

# SCOPE_OK=0 means we couldn't determine what changed (no resolvable base
# ref). That degrades file-scoped ruff to skipped, but backend/frontend
# checks run unconditionally, whole-project, rather than also skipping —
# fail-closed instead of the gate silently doing nothing.
SCOPE_OK=1
BASE_REF=main
if ! git rev-parse --verify --quiet "$BASE_REF" >/dev/null 2>&1; then
  BASE_REF=origin/main
fi
if ! git rev-parse --verify --quiet "$BASE_REF" >/dev/null 2>&1; then
  echo "WARNING: could not resolve 'main' or 'origin/main' — skipping file-scoped ruff; running the full backend + frontend suites unconditionally instead." >&2
  BASE_REF=""
  SCOPE_OK=0
fi

PY_FILES=()
FE_FILES=()
if [ "$SCOPE_OK" -eq 1 ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && PY_FILES+=("$line")
  done < <(git diff --name-only --diff-filter=ACMR "$BASE_REF"...HEAD -- '*.py')

  while IFS= read -r line; do
    [ -n "$line" ] && FE_FILES+=("$line")
  done < <(git diff --name-only --diff-filter=ACMR "$BASE_REF"...HEAD -- 'frontend/*.ts' 'frontend/*.tsx' 'frontend/*.js' 'frontend/*.jsx' 'frontend/*.mjs' 'frontend/*.json' 'frontend/*.html' 'frontend/*.css' 'tests/frontend/*.mjs')
fi

if [ "$SCOPE_OK" -eq 1 ] && [ "${#PY_FILES[@]}" -gt 0 ]; then
  {
    echo "== poetry run ruff format --check (changed files) =="
    run_with_timeout 60 poetry run ruff format --check -- "${PY_FILES[@]}" || FAIL=1
    echo "== poetry run ruff check (changed files) =="
    run_with_timeout 60 poetry run ruff check -- "${PY_FILES[@]}" || FAIL=1
  } >>"$LOG" 2>&1
fi

# Fail fast on the cheap check before paying for the full backend + frontend
# suites — a one-line format nit shouldn't cost a multi-minute double run.
if [ "$FAIL" -ne 0 ]; then
  echo "BLOCKED: git push — ruff format/lint failed (skipping tests). Last 80 lines:" >&2
  tail -80 "$LOG" >&2
  exit 2
fi

RUN_BACKEND=0
if [ "$SCOPE_OK" -eq 0 ] || [ "${#PY_FILES[@]}" -gt 0 ]; then
  RUN_BACKEND=1
fi

# Backend tests need the throwaway Postgres on :5544 (see CLAUDE.md — NEVER
# point this at dev DB :5433). If Python changed (or scope is unknown) and
# the DB isn't reachable, that's treated as a failed check, not a skip —
# PUSH_GATE_SKIP_TESTS=1 is the explicit, visible opt-out for a deliberate
# local bypass.
if [ "$RUN_BACKEND" -eq 1 ]; then
  if command -v pg_isready >/dev/null 2>&1 && pg_isready -h localhost -p 5544 >/dev/null 2>&1; then
    echo "== poetry run pytest (DATABASE_URL -> :5544 test DB) ==" >>"$LOG"
    if ! run_with_timeout 420 env DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test GROQ_API_KEY=test-key \
        poetry run pytest -x -q >>"$LOG" 2>&1; then
      FAIL=1
    fi
  elif [ "${PUSH_GATE_SKIP_TESTS:-0}" = "1" ]; then
    echo "WARNING: throwaway test DB (:5544) not reachable — PUSH_GATE_SKIP_TESTS=1 set, skipping backend tests for this push (deliberate opt-out)." >&2
  else
    echo "== backend tests skipped: throwaway test DB (:5544) not reachable ==" >>"$LOG"
    echo "BLOCKED: git push — throwaway test DB (:5544) not reachable, cannot verify backend tests pass. Start it (see CLAUDE.md) or set PUSH_GATE_SKIP_TESTS=1 to explicitly skip (not recommended)." >&2
    FAIL=1
  fi
fi

RUN_FRONTEND=0
if { [ "$SCOPE_OK" -eq 0 ] || [ "${#FE_FILES[@]}" -gt 0 ]; } && [ -d "$CLAUDE_PROJECT_DIR/frontend" ]; then
  RUN_FRONTEND=1
fi

if [ "$RUN_FRONTEND" -eq 1 ]; then
  {
    echo "== npm run typecheck (whole project — tsc project refs can't be file-scoped) =="
    run_with_timeout 90 bash -c "cd '$CLAUDE_PROJECT_DIR/frontend' && npm run typecheck" || FAIL=1
    echo "== npm run test =="
    run_with_timeout 120 bash -c "cd '$CLAUDE_PROJECT_DIR/frontend' && npm run test" || FAIL=1
    echo "== npm run lint (whole project — matches CI, a file-scoped eslint call can miss project config) =="
    run_with_timeout 60 bash -c "cd '$CLAUDE_PROJECT_DIR/frontend' && npm run lint" || FAIL=1
    echo "== npm run lint:i18n =="
    run_with_timeout 30 bash -c "cd '$CLAUDE_PROJECT_DIR/frontend' && npm run lint:i18n" || FAIL=1
    echo "== npm run lint:i18n-strings =="
    run_with_timeout 30 bash -c "cd '$CLAUDE_PROJECT_DIR/frontend' && npm run lint:i18n-strings" || FAIL=1
    echo "== npm run test:check-entry-chunk (fixture-based positive/negative controls for the checker itself) =="
    run_with_timeout 30 bash -c "cd '$CLAUDE_PROJECT_DIR/frontend' && npm run test:check-entry-chunk" || FAIL=1
    if [ "${PUSH_GATE_SKIP_BUILD:-0}" = "1" ]; then
      echo "WARNING: PUSH_GATE_SKIP_BUILD=1 set — skipping npm run build:bundle + check:entry-chunk for this push (deliberate opt-out; MapLibre-in-entry regressions won't be caught locally)." >&2
    else
      echo "== npm run build:bundle && npm run check:entry-chunk (MapLibre must stay out of the entry chunk; typecheck already ran above, so this build step skips tsc -b) =="
      run_with_timeout 480 bash -c "cd '$CLAUDE_PROJECT_DIR/frontend' && npm run build:bundle && npm run check:entry-chunk"
      rc=$?
      if [ "$rc" -eq 124 ]; then
        echo "frontend build/check-entry-chunk TIMED OUT after 480s (not a build or check failure)." >&2
      fi
      if [ "$rc" -ne 0 ]; then
        FAIL=1
      fi
    fi
  } >>"$LOG" 2>&1
fi

if [ "$FAIL" -ne 0 ]; then
  echo "BLOCKED: git push — quality gate failed. Last 80 lines:" >&2
  tail -80 "$LOG" >&2
  exit 2
fi

exit 0
