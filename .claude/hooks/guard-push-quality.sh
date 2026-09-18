#!/usr/bin/env bash
# PreToolUse(Bash) hook: gate `git push` on lint/test passing first.
# Lint/format checks are scoped to files changed vs `main` (the repo has
# pre-existing lint/format debt elsewhere, so a whole-repo gate would block
# every push); tests, mypy, and frontend checks run whole-project since those
# can't be meaningfully file-scoped. Fails CLOSED: anywhere this script can't
# determine what changed or can't run a required check, it blocks (exit 2)
# rather than silently letting the push through — set PUSH_GATE_SKIP_TESTS=1
# for a deliberate, visible opt-out of the DB-dependent backend tests only,
# or PUSH_GATE_SKIP_BUILD=1 to skip the frontend build:bundle + entry-chunk
# check specifically.
#
# Scope limitation, stated because it is not obvious and is easy to mistake
# for coverage: the file-scoped ruff checks read the branch being pushed (see
# $GATE_DIR below), but mypy, the backend tests and the frontend checks run
# against $CLAUDE_PROJECT_DIR's own working tree. For a push from a worktree
# those validate whatever the main checkout currently holds, not the branch.
# Running them in the worktree instead would need a provisioned virtualenv
# and node_modules there, which a worktree does not inherit. Until that is
# solved, `scripts/run_full_ci.sh` from the worktree covers the backend
# against the branch — it mirrors CI's `test` job only, so CI's separate
# frontend job has no worktree-runnable equivalent and a frontend change
# still has nothing checking it against the branch being pushed.
#
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

# Every branch in this repo is developed in its own worktree, so the push
# being gated is almost never from $CLAUDE_PROJECT_DIR — that checkout sits
# on `main`, where `main...HEAD` is empty. Checking there scopes ruff to zero
# files and skips it entirely while still reporting success: the gate passes
# having inspected nothing. Resolve the directory the push actually comes
# from, in decreasing order of reliability, and verify it belongs to this
# same repository before trusting it.
GATE_DIR=""
same_repo() {
  [ -d "$1" ] || return 1
  local theirs ours
  theirs="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  ours="$(git -C "$CLAUDE_PROJECT_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  [ "$theirs" = "$ours" ]
}

unquote() {
  # Strip one matching layer of quotes. A quoted path is mandatory when it
  # contains a space and common style when it does not, and the capture keeps
  # the quote characters, which no directory name has.
  local s="$1"
  case "$s" in
    \"*\") s="${s#\"}"; s="${s%\"}" ;;
    \'*\') s="${s#\'}"; s="${s%\'}" ;;
  esac
  printf '%s' "$s"
}

# Only the text up to the `push` token is searched for a directory, so a
# compound command's later, unrelated `git -C <elsewhere> status` cannot be
# mistaken for where the push happens.
PUSH_PREFIX="$(printf '%s' "$cmd" | sed -nE 's/(.*)[[:space:]]push([[:space:]].*)?$/\1/p' | head -1)"
[ -n "$PUSH_PREFIX" ] || PUSH_PREFIX="$cmd"

# The command's own directive wins over the payload's `cwd`: `cwd` is the
# session's directory, which stays at the main checkout even for a command
# that cds into a worktree first — so trusting it would re-introduce exactly
# the misdirection being fixed.
# No `\b` or `\s` in these patterns: BSD sed (macOS, where this hook also
# runs) supports neither, and silently matches nothing instead of erroring.
NAMED_DIR=""
for pattern in \
  's/.*-C[[:space:]]+"([^"]+)".*/\1/p' \
  "s/.*-C[[:space:]]+'([^']+)'.*/\\1/p" \
  's/.*-C[[:space:]]+([^[:space:]]+).*/\1/p' \
  's/^[[:space:]]*cd[[:space:]]+"([^"]+)".*/\1/p' \
  "s/^[[:space:]]*cd[[:space:]]+'([^']+)'.*/\\1/p" \
  's/^[[:space:]]*cd[[:space:]]+([^[:space:]&;|]+).*/\1/p'
do
  found="$(printf '%s' "$PUSH_PREFIX" | sed -nE "$pattern" | head -1)"
  [ -n "$found" ] || continue
  NAMED_DIR="$(unquote "$found")"
  if same_repo "$NAMED_DIR"; then
    GATE_DIR="$NAMED_DIR"
    break
  fi
done

# A named directory that did not match this repository is judged on whether it
# exists at all. A path that does not exist is the signature of parsing this
# gate got wrong — a spelling it does not cover (an escaped space, a variable,
# a relative path) reduced to something meaningless — and silently continuing
# from there is precisely how the original defect behaved, so it is reported
# instead. A path that does exist is simply not this repository's push, and
# gating someone else's repository is not this hook's business.
if [ -z "$GATE_DIR" ] && [ -n "$NAMED_DIR" ]; then
  if [ -e "$NAMED_DIR" ]; then
    exit 0
  fi
  echo "BLOCKED: git push — the command names directory '$NAMED_DIR', which does not exist, so" >&2
  echo "  this gate cannot tell which branch's files to check. That usually means the path was" >&2
  echo "  written in a form it failed to read; pass it unquoted and absolute." >&2
  exit 2
fi

if [ -z "$GATE_DIR" ]; then
  hook_cwd="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("cwd") or "")' 2>/dev/null)"
  if [ -n "$hook_cwd" ] && same_repo "$hook_cwd"; then
    GATE_DIR="$hook_cwd"
  fi
fi
[ -n "$GATE_DIR" ] || GATE_DIR="$CLAUDE_PROJECT_DIR"

# Stay in $CLAUDE_PROJECT_DIR to RUN the tools, and read the file list from
# $GATE_DIR: poetry resolves its virtualenv by cwd identity, so running from a
# worktree picks up that worktree's own — usually unprovisioned — environment
# and would turn this gate's silent false pass into an equally useless false
# block. The changed files are therefore passed as absolute paths under
# $GATE_DIR instead; ruff reads its configuration from each file's own nearest
# pyproject.toml, which is the same file in either checkout.
cd "$CLAUDE_PROJECT_DIR" || { echo "BLOCKED: git push — could not cd to \$CLAUDE_PROJECT_DIR ($CLAUDE_PROJECT_DIR)." >&2; exit 2; }
if [ "$GATE_DIR" != "$CLAUDE_PROJECT_DIR" ]; then
  echo "== push gate: files from $GATE_DIR, tools from $CLAUDE_PROJECT_DIR ==" >&2
fi

LOG="$(mktemp)"
# run_with_timeout's fallback path (below) creates a marker temp file per
# call; if this script's process receives a catchable termination (an
# external harness timeout, SIGTERM, or a normal early exit) after a marker
# is created but before that call's own cleanup runs, it would otherwise
# leak. Accumulate every marker path here so the EXIT trap sweeps them up
# alongside $LOG. (A literal SIGKILL still leaks the marker regardless --
# no shell trap, including EXIT, can catch it; same pre-existing limitation
# $LOG's own cleanup already had.)
MARKER_FILES=()
# Count-guard the array expansion: bash 3.2 (macOS's default /bin/bash,
# with no Homebrew coreutils -- exactly the environment run_with_timeout's
# fallback branch targets) treats "${MARKER_FILES[@]}" on an empty array as
# an unbound-variable error under `set -u`. $LOG's own removal (the first
# statement) isn't affected -- it's the second statement that would abort
# with a stray stderr message, leaving that call's marker file unswept.
# Bash >=4.4 doesn't need this guard, but 3.2 does, so guard for both.
trap 'rm -f "$LOG"; [ "${#MARKER_FILES[@]}" -eq 0 ] || rm -f "${MARKER_FILES[@]}"' EXIT
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
  MARKER_FILES+=("$marker")
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

PY_PATHSPEC=('*.py')
FE_PATHSPEC=(
  'frontend/*.ts' 'frontend/*.tsx' 'frontend/*.js' 'frontend/*.jsx' 'frontend/*.mjs'
  'frontend/*.json' 'frontend/*.html' 'frontend/*.css' 'tests/frontend/*.mjs'
)

PY_FILES=()
FE_FILES=()
# `git -C "$GATE_DIR"` for every diff below: HEAD has to mean the branch being
# pushed, not whatever $CLAUDE_PROJECT_DIR happens to sit on. Paths come back
# repo-relative, so they are anchored to $GATE_DIR to stay valid from here.
if [ "$SCOPE_OK" -eq 1 ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && PY_FILES+=("$GATE_DIR/$line")
  done < <(git -C "$GATE_DIR" diff --name-only --diff-filter=ACMR "$BASE_REF"...HEAD -- "${PY_PATHSPEC[@]}")

  while IFS= read -r line; do
    [ -n "$line" ] && FE_FILES+=("$GATE_DIR/$line")
  done < <(git -C "$GATE_DIR" diff --name-only --diff-filter=ACMR "$BASE_REF"...HEAD -- "${FE_PATHSPEC[@]}")
fi

# "Nothing changed here" is the shape a misdirected gate takes, so it cannot
# be accepted on the word of a directory we only guessed at. When the push
# names a branch that does carry changes, this directory is the wrong one and
# the scoped checks below would inspect nothing while still reporting success.
#
# Deleting a remote branch is the exception: it legitimately adds no files, so
# the named branch still differing from base says nothing about whether this
# directory is the right one. Treated separately because the heuristic below
# would otherwise refuse every `git push origin :branch`.
IS_DELETE=0
if printf '%s' "$cmd" | grep -Eq '(^| )--delete( |$)|(^| )-d( |$)| :[^ ]' ; then
  IS_DELETE=1
fi
if [ "$IS_DELETE" -eq 0 ] && [ "$SCOPE_OK" -eq 1 ] && [ "${#PY_FILES[@]}" -eq 0 ] && [ "${#FE_FILES[@]}" -eq 0 ]; then
  # Collect the arguments after the `push` TOKEN. Cutting the string at the
  # text "push" instead would cut at the last occurrence, which lands inside
  # any branch name containing it (`fix/push-gate-...`) and leaves the list
  # empty — silently disabling this very check for such a branch.
  # `read -ra`, not `for tok in $cmd`: the latter also expands globs, and the
  # script has already cd'd into the repo — so `git push origin 'refs/heads/*'`
  # would turn into a list of matching filenames, dropping the real ref.
  refs=()
  read -ra cmd_tokens <<<"$cmd"
  seen_push=0
  for tok in ${cmd_tokens[@]+"${cmd_tokens[@]}"}; do
    if [ "$seen_push" -eq 1 ]; then
      case "$tok" in
        -*|origin) ;;
        *) refs+=("$tok") ;;
      esac
    fi
    [ "$tok" = "push" ] && seen_push=1
  done

  for ref in ${refs[@]+"${refs[@]}"}; do
    # Take the destination half of a `src:dst` refspec, then drop a
    # `refs/heads/` prefix so the fully-qualified form resolves too.
    branch="${ref##*:}"
    branch="${branch#refs/heads/}"
    git rev-parse --verify --quiet "refs/heads/$branch" >/dev/null 2>&1 || continue
    # Filtered to the same pathspecs the scoped checks use: a branch that
    # changes only shell, SQL or Markdown legitimately produces no files for
    # them, and comparing against its whole diff would refuse that push.
    if [ -n "$(git diff --name-only "$BASE_REF...refs/heads/$branch" -- "${PY_PATHSPEC[@]}" "${FE_PATHSPEC[@]}" 2>/dev/null)" ]; then
      echo "BLOCKED: git push — the gate is running in $GATE_DIR, where nothing differs from $BASE_REF," >&2
      echo "  but branch '$branch' does differ. The scoped checks would inspect no files and pass" >&2
      echo "  without verifying anything. Push from the worktree holding '$branch', or use" >&2
      echo "  'git -C <that worktree> push ...' so this gate can find it:" >&2
      git worktree list | sed 's/^/    /' >&2
      exit 2
    fi
  done
fi

if [ "$SCOPE_OK" -eq 1 ] && [ "${#PY_FILES[@]}" -gt 0 ]; then
  # Each line needs both -- separators: the first marks the boundary between
  # poetry's own option parsing and the wrapped command's argv (without it,
  # some poetry versions misread a flag appearing before the wrapped
  # command's own -- as an unrecognized poetry option instead of forwarding
  # it); the second is ruff's own end-of-flags marker so a dash-prefixed
  # filename is read as a path, not a ruff flag. Collapsing either back to
  # one -- has silently broken this before -- do not simplify.
  {
    echo "== poetry run ruff format --check (changed files) =="
    run_with_timeout 60 poetry run -- ruff format --check -- "${PY_FILES[@]}" || FAIL=1
    echo "== poetry run ruff check (changed files) =="
    run_with_timeout 60 poetry run -- ruff check -- "${PY_FILES[@]}" || FAIL=1
  } >>"$LOG" 2>&1
fi

# mypy runs whole-project, not file-scoped: a type error is a property of a
# module and of everything importing it, so checking only the changed files
# would miss the breakage a changed signature causes in its callers. Unlike
# ruff (which has pre-existing debt outside the changed set, hence the file
# scoping above), the configured mypy scope in pyproject.toml is clean, so a
# whole-project run blocks only on a real regression. Same trigger as the
# backend tests below — Python changed, or scope couldn't be resolved.
if [ "$SCOPE_OK" -eq 0 ] || [ "${#PY_FILES[@]}" -gt 0 ]; then
  {
    echo "== poetry run mypy (whole configured scope) =="
    run_with_timeout 180 poetry run -- mypy || FAIL=1
  } >>"$LOG" 2>&1
fi

# Fail fast on the cheap checks before paying for the full backend + frontend
# suites — a one-line format nit shouldn't cost a multi-minute double run.
if [ "$FAIL" -ne 0 ]; then
  echo "BLOCKED: git push — ruff format/lint or mypy failed (skipping tests). Last 80 lines:" >&2
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
    # The whole backend suite is this gate's long pole by an order of
    # magnitude, and every test builds its schema against the throwaway DB,
    # so the ceiling has to clear the suite's real wall-clock with room for
    # it to keep growing. Set too tight, the timeout fires on every Python
    # change and the gate never reports a genuine pass -- pushes then either
    # look broken or get routed around, which is strictly worse than no gate.
    #
    # Every ceiling in this script is bounded by one more: the `timeout` on
    # this hook's entry in .claude/settings.json, enforced by the harness
    # rather than by this script. If the ceilings here can sum past it, the
    # harness kills the script before it reaches its own exit, and whether
    # that blocks the push or lets it through is outside this script's
    # control -- the one outcome its fail-closed design cannot guarantee.
    # Raise that entry alongside any ceiling raised here.
    if ! run_with_timeout 1200 env DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test GEMINI_API_KEY=test-key \
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
