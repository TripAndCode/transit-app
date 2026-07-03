#!/usr/bin/env bash
# PreToolUse(Bash) hook: gate `git push` on lint/test passing first.
# Lint/format checks are scoped to files changed vs `main` (the repo has
# pre-existing lint/format debt elsewhere, so a whole-repo gate would block
# every push); tests and frontend typecheck run whole-project since those
# can't be meaningfully file-scoped. Reads the tool input JSON on stdin;
# exit 2 = block the tool call.
set -uo pipefail
input="$(cat)"

# Cheap pre-filter on the raw JSON before paying for a python3 spawn — the
# command text is embedded verbatim, so a raw substring match is a safe
# superset of the real check below.
printf '%s' "$input" | grep -q 'push' || exit 0

cmd="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)"
# Deliberately loose ("git" ... "push" as words, in order) rather than
# anchored to line-start — anchoring missed `FOO=bar git push`, `git -c
# x=y push`, `(git push)`, and multi-space forms. Over-matching (e.g. a
# commit message containing "git push") just re-runs checks; under-matching
# would let an ungated push through, which is the failure mode that matters.
printf '%s' "$cmd" | grep -Eq '\bgit\b.*\bpush\b' || exit 0

cd "$CLAUDE_PROJECT_DIR" || exit 0
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
FAIL=0

BASE_REF=main
if ! git rev-parse --verify --quiet "$BASE_REF" >/dev/null 2>&1; then
  BASE_REF=origin/main
fi
if ! git rev-parse --verify --quiet "$BASE_REF" >/dev/null 2>&1; then
  echo "WARNING: could not resolve 'main' or 'origin/main' — push-gate lint/format scoping skipped (tests and frontend typecheck still run whole-project)." >&2
  BASE_REF=""
fi

PY_FILES=()
FE_FILES=()
if [ -n "$BASE_REF" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && PY_FILES+=("$line")
  done < <(git diff --name-only --diff-filter=ACMR "$BASE_REF"...HEAD -- '*.py')

  while IFS= read -r line; do
    [ -n "$line" ] && FE_FILES+=("$line")
  done < <(git diff --name-only --diff-filter=ACMR "$BASE_REF"...HEAD -- 'frontend/*.ts' 'frontend/*.tsx')
fi

if [ "${#PY_FILES[@]}" -gt 0 ]; then
  {
    echo "== poetry run ruff format --check (changed files) =="
    poetry run ruff format --check -- "${PY_FILES[@]}" || FAIL=1
    echo "== poetry run ruff check (changed files) =="
    poetry run ruff check -- "${PY_FILES[@]}" || FAIL=1
  } >>"$LOG" 2>&1
fi

# Backend tests need the throwaway Postgres on :5544 (see CLAUDE.md — NEVER point
# this at dev DB :5433). Only run them if this branch touched Python AND that
# container is actually reachable; otherwise warn instead of hard-blocking.
if [ "${#PY_FILES[@]}" -gt 0 ]; then
  if command -v pg_isready >/dev/null 2>&1 && pg_isready -h localhost -p 5544 >/dev/null 2>&1; then
    echo "== poetry run pytest (DATABASE_URL -> :5544 test DB) ==" >>"$LOG"
    if ! DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test GROQ_API_KEY=test-key \
        poetry run pytest >>"$LOG" 2>&1; then
      FAIL=1
    fi
  else
    echo "WARNING: throwaway test DB (:5544) not reachable — skipped backend tests for this push gate. Run \`make test\` against :5544 manually before trusting this push." >&2
  fi
fi

if [ "${#FE_FILES[@]}" -gt 0 ] && [ -d "$CLAUDE_PROJECT_DIR/frontend" ]; then
  {
    echo "== npm run typecheck (whole project — tsc project refs can't be file-scoped) =="
    (cd "$CLAUDE_PROJECT_DIR/frontend" && npm run typecheck) || FAIL=1
    echo "== npm run test =="
    (cd "$CLAUDE_PROJECT_DIR/frontend" && npm run test) || FAIL=1
    echo "== npm run lint (whole project — matches CI, a file-scoped eslint call can miss project config) =="
    (cd "$CLAUDE_PROJECT_DIR/frontend" && npm run lint) || FAIL=1
    echo "== npm run lint:i18n =="
    (cd "$CLAUDE_PROJECT_DIR/frontend" && npm run lint:i18n) || FAIL=1
    echo "== npm run lint:i18n-strings =="
    (cd "$CLAUDE_PROJECT_DIR/frontend" && npm run lint:i18n-strings) || FAIL=1
  } >>"$LOG" 2>&1
fi

if [ "$FAIL" -ne 0 ]; then
  echo "BLOCKED: git push — quality gate failed. Last 80 lines:" >&2
  tail -80 "$LOG" >&2
  exit 2
fi

exit 0
