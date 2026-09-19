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

# The command is tokenised and its statements walked, rather than searched
# with a regex. A regex over the raw text cannot tell which `git` invocation a
# flag belongs to, so `ls -C /tmp && git push` or a commit message quoting
# `-C /tmp` read as a directory directive, and `grep -C 3 …` read as one that
# does not exist. shlex applies the shell's own quoting rules, so a quoted or
# escaped path arrives intact, and walking the statement that actually carries
# the `push` subcommand is what makes a flag elsewhere in the line irrelevant.
PARSED="$(
  printf '%s' "$input" | python3 -c '
import json, shlex, sys

SEPARATORS = {"&&", "||", ";", "|", "&"}
out = {"dir": "", "cd_dir": "", "refs": [], "is_delete": False}
try:
    data = json.load(sys.stdin)
    raw_cmd = data.get("tool_input", {}).get("command", "")
    # A newline outside quotes is a statement separator exactly like ";" --
    # shlex.split() alone treats it as ordinary whitespace, so a multi-line
    # command (an entirely normal way to author a compound Bash-tool call,
    # e.g. a "cd <worktree>" line followed by a "git push ..." line)
    # collapses into a single unsplit statement, and every heuristic below
    # that keys off a statement first token ("git", "cd") silently stops
    # firing. A newline inside a quoted string (a multi-line commit message)
    # is left alone so its content is not altered, and an unquoted
    # backslash-newline pair (a real shell line continuation) is dropped
    # entirely rather than turned into a literal newline character --
    # shlex.split() has no concept of line continuation itself, so passing
    # "\<newline>" straight through leaves a literal newline glued onto
    # whatever token follows instead of eliding it the way a real shell
    # would. chr(34)/chr(39) stand in for literal double/single-quote
    # characters here because this whole script is itself embedded in a
    # single-quoted shell argument.
    dq, sq = chr(34), chr(39)
    quote, chars = None, []
    i, n = 0, len(raw_cmd)
    while i < n:
        ch = raw_cmd[i]
        if quote:
            chars.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == dq and i + 1 < n:
                i += 1
                chars.append(raw_cmd[i])
        elif ch == "\\" and i + 1 < n and raw_cmd[i + 1] == "\n":
            i += 1  # line continuation: both characters vanish
        elif ch == "\\" and i + 1 < n:
            chars.append(ch)
            i += 1
            chars.append(raw_cmd[i])
        elif ch in (sq, dq):
            quote = ch; chars.append(ch)
        elif ch == "\n":
            # Surrounding spaces matter: SEPARATORS below only matches a
            # standalone ";" token, and shlex.split() has no idea ";" is
            # special -- glued to a neighbour with no whitespace it is just
            # another character in that word, same as "cmd1;cmd2" would be.
            chars.append(" ; ")
        else:
            chars.append(ch)
        i += 1
    tokens = shlex.split("".join(chars))
except Exception:
    print(json.dumps(out)); raise SystemExit(0)

statements, current = [], []
for token in tokens:
    if token in SEPARATORS:
        if current:
            statements.append(current)
        current = []
    else:
        current.append(token)
if current:
    statements.append(current)


def as_git(statement):
    """(dir_from_dash_C, subcommand, args) for a git invocation, else None."""
    i = 0
    while i < len(statement) and "=" in statement[i] and not statement[i].startswith("-"):
        i += 1  # leading VAR=value assignments
    if i >= len(statement) or statement[i] != "git":
        return None
    i += 1
    directory = None
    while i < len(statement) and statement[i].startswith("-"):
        if statement[i] in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(statement):
            if statement[i] == "-C":
                directory = statement[i + 1]
            i += 2
        else:
            i += 1
    if i >= len(statement):
        return None
    return directory, statement[i], statement[i + 1 :]


for statement in statements:
    parsed = as_git(statement)
    if parsed and parsed[1] == "push":
        directory, _, args = parsed
        out["dir"] = directory or ""
        out["is_delete"] = any(a in ("--delete", "-d") for a in args) or any(
            a.startswith(":") and len(a) > 1 for a in args
        )
        out["refs"] = [a for a in args if not a.startswith("-") and a != "origin"]
        break
    # A later cd overrides an earlier one, as it would in the shell.
    if statement[:1] == ["cd"] and len(statement) > 1:
        out["cd_dir"] = statement[1]

print(json.dumps(out))
' 2>/dev/null
)"
[ -n "$PARSED" ] || PARSED='{}'

read_parsed() {
  printf '%s' "$PARSED" | python3 -c "import json,sys; v=json.load(sys.stdin).get('$1'); print('' if v is None else (' '.join(v) if isinstance(v, list) else v))" 2>/dev/null
}

# The push's own `-C` is the most explicit statement of where it runs, so it
# is resolved definitively right here rather than being allowed to fall
# through to a later tier: an explicit `-C` on the statement that actually
# carries `push` is unambiguous about which repository that git invocation
# touches, and nothing else in the command -- a ref name, a preceding `cd`
# -- can override it. Without this early exit, a foreign-but-real `-C`
# (one that exists and resolves, just not to this repository) that failed
# to match here would fall through to tier 2/3 below, which could then
# substitute some unrelated directory the rest of the command happens to
# also mention -- checking a repository/branch the push never actually
# touches while never inspecting its real target at all.
EXPLICIT_C_DIR="$(read_parsed dir)"
if [ -n "$EXPLICIT_C_DIR" ]; then
  if same_repo "$EXPLICIT_C_DIR"; then
    GATE_DIR="$EXPLICIT_C_DIR"
  elif [ -e "$EXPLICIT_C_DIR" ]; then
    # Exists, just isn't this repository -- gating someone else's
    # repository is not this hook's business.
    exit 0
  else
    echo "BLOCKED: git push — the command names directory '$EXPLICIT_C_DIR', which does not exist, so" >&2
    echo "  this gate cannot tell which branch's files to check. That usually means the path was" >&2
    echo "  written in a form it failed to read; pass it unquoted and absolute." >&2
    exit 2
  fi
fi

# Then the branch being pushed, which identifies the worktree holding it. This
# outranks a preceding `cd` because it says where the pushed FILES are rather
# than where the command happens to run: pushing a branch from the main
# checkout is ordinary, and the branch's content still lives in its worktree.
# It also needs nothing from the command's shape.
if [ -z "$GATE_DIR" ]; then
  for ref in $(read_parsed refs); do
    # Take the SOURCE half of a `src:dst` refspec: that names the local
    # branch/worktree actually being pushed, not where it lands remotely.
    branch="${ref%%:*}"
    branch="${branch#refs/heads/}"
    # A literal "HEAD" (`git push origin HEAD`) or an empty token can't be
    # resolved here -- this tier finds a directory FROM a branch name, and
    # neither is one. That's fine: it falls through to the `cd`/cwd tiers
    # below, which resolve HEAD from a directory instead of the other way
    # around.
    [ -n "$branch" ] && [ "$branch" != "HEAD" ] || continue
    # `-C "$CLAUDE_PROJECT_DIR"` matters here: this tier runs before the
    # script ever `cd`s anywhere, so an unscoped `git worktree list` would
    # depend on the invoking shell's own cwd -- which is exactly the kind
    # of unreliable location this whole hook exists to route around, not
    # something a lookup tier should quietly inherit.
    holder="$(git -C "$CLAUDE_PROJECT_DIR" worktree list --porcelain | awk -v want="refs/heads/$branch" '
      /^worktree /{dir=substr($0, 10)}
      /^branch /{if (substr($0, 8) == want) {print dir; exit}}')"
    if [ -n "$holder" ] && same_repo "$holder"; then
      GATE_DIR="$holder"
      break
    fi
  done
fi

# Then a preceding `cd`, for a push that names no ref at all. (The explicit
# `-C` case above already either set $GATE_DIR or exited, so reaching here
# means there was no `-C` on the push statement at all -- $CD_DIR is the
# only remaining directory candidate.)
CD_DIR=""
if [ -z "$GATE_DIR" ]; then
  CD_DIR="$(read_parsed cd_dir)"
  if [ -n "$CD_DIR" ] && same_repo "$CD_DIR"; then
    GATE_DIR="$CD_DIR"
  fi
fi

# A named `cd` directory that did not match this repository is judged on
# whether it exists at all. A path that does not exist is the signature of
# parsing this gate got wrong — a spelling it does not cover (an escaped
# space, a variable, a relative path) reduced to something meaningless —
# and silently continuing from there is precisely how the original defect
# behaved, so it is reported instead. A path that does exist is simply not
# this repository's push, and gating someone else's repository is not this
# hook's business.
if [ -z "$GATE_DIR" ] && [ -n "$CD_DIR" ]; then
  if [ -e "$CD_DIR" ]; then
    exit 0
  fi
  echo "BLOCKED: git push — the command names directory '$CD_DIR', which does not exist, so" >&2
  echo "  this gate cannot tell which branch's files to check. That usually means the path was" >&2
  echo "  written in a form it failed to read; pass it unquoted and absolute." >&2
  exit 2
fi

if [ -z "$GATE_DIR" ]; then
  # Last resort for a push whose own argv names no directory and no branch
  # (a bare `git push` with no leading `cd`/`-C` in this same command) --
  # the payload's cwd, which reflects wherever the session's shell state
  # currently sits, including a `cd` done in an earlier, separate tool
  # call. If that ever stops being accurate for some payload shape, there
  # is nothing left in the command text for this hook to fall back to: a
  # push this contextless is, by construction, indistinguishable from one
  # made from $CLAUDE_PROJECT_DIR itself.
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

# A branch every one of whose commits suppresses CI produces no run at all, so
# the PR has nothing for the merge gate to read. Only the tip of the push is
# consulted, which is the part that is easy to get wrong: a trailer-less commit
# buried earlier in the branch changes nothing. A warning, not a block —
# suppressing CI on intermediate pushes is the normal case, and only the last
# push before readying has to differ.
#
# The token is assembled rather than written out because this file's own
# content would otherwise land in a commit message quoting it, and the match
# is a plain substring.
# Not for a deletion: `git push origin :branch` resolves no branch, so
# $GATE_DIR fell back to whatever checkout the command ran from, and the tip
# reported on would be that checkout's, unrelated to what is being deleted.
SKIP_TOKEN="[skip"" ci]"
tip_msg=""
[ "$(read_parsed is_delete)" = "True" ] || tip_msg="$(git -C "$GATE_DIR" log -1 --format=%B 2>/dev/null)"
case "$tip_msg" in
  *"$SKIP_TOKEN"*)
    echo "NOTE: this push's tip suppresses CI, so no run will appear for it." >&2
    echo "  Before marking the PR ready, push a tip whose message omits that" >&2
    echo "  trailer — the merge gate needs a green run to read." >&2
    ;;
esac

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
#
# Both this and the ref list come from the parsed push argv, not from the raw
# text: a `-d` belonging to something else on the line (`docker run -d …`)
# would otherwise read as a deletion and switch this whole check off.
IS_DELETE=0
[ "$(read_parsed is_delete)" = "True" ] && IS_DELETE=1
if [ "$IS_DELETE" -eq 0 ] && [ "$SCOPE_OK" -eq 1 ] && [ "${#PY_FILES[@]}" -eq 0 ] && [ "${#FE_FILES[@]}" -eq 0 ]; then
  # A literal `HEAD` (`git push origin HEAD`) or a completely bare
  # `git push` (relying on the branch's own upstream tracking) cannot be
  # checked by this safety net: both mean "whatever branch GATE_DIR is
  # actually on", which is exactly the value this net exists to
  # cross-check GATE_DIR against. Resolving "HEAD" from GATE_DIR itself
  # would just read back GATE_DIR's own branch, which can never disagree
  # with itself -- there is no independent second data point in the
  # command's own argv for either shape, so `read_parsed refs` returning
  # nothing or a lone "HEAD" is left to fall through this loop untouched
  # (the same as it always could not be checked before this comment was
  # written). This is a real, accepted gap, not something a smarter
  # regex or resolution step can close from parsing alone.
  for ref in $(read_parsed refs); do
    # Take the SOURCE half of a `src:dst` refspec, then drop a
    # `refs/heads/` prefix so the fully-qualified form resolves too.
    branch="${ref%%:*}"
    branch="${branch#refs/heads/}"
    git rev-parse --verify --quiet "refs/heads/$branch" >/dev/null 2>&1 || continue
    # Filtered to the same pathspecs the scoped checks use (and with the
    # same --diff-filter=ACMR as PY_FILES/FE_FILES above): a branch whose
    # only Python/frontend change is a deletion, or one that changes only
    # shell/SQL/Markdown, legitimately produces no files here, and
    # comparing against its unfiltered diff would refuse both pushes for
    # a directory that was never actually wrong.
    if [ -n "$(git diff --name-only --diff-filter=ACMR "$BASE_REF...refs/heads/$branch" -- "${PY_PATHSPEC[@]}" "${FE_PATHSPEC[@]}" 2>/dev/null)" ]; then
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
