#!/usr/bin/env bash
# PreToolUse(Bash) hook: gate `git push` on lint/test passing first.
# Ruff runs first on the changed files alone, so a formatting nit fails in
# seconds; mypy, the backend suite and the frontend checks run whole-project
# since those can't be meaningfully file-scoped. Fails CLOSED: anywhere this
# script can't determine what changed or can't run a required check, it
# blocks (exit 2) rather than silently letting the push through — set
# PUSH_GATE_SKIP_TESTS=1 for a deliberate, visible opt-out of the
# container-backed backend suite (and of mypy too, when a dependency change
# leaves no virtualenv to run it in), or PUSH_GATE_SKIP_BUILD=1 to skip the
# frontend build:bundle + entry-chunk check specifically. Both are read from
# this hook's own environment, which the harness spawns: they must be in the
# Claude Code session's environment (settings `env`, or the launch
# environment). A `VAR=1 git push ...` prefix reaches git only, never the hook.
#
# Every check reads the branch being pushed ($GATE_DIR below), never whatever
# $CLAUDE_PROJECT_DIR's own working tree holds. A worktree inherits neither a
# virtualenv nor node_modules, so the tools come from the main checkout: a
# `poetry` shim routes `poetry run` to its virtualenv, and its node_modules is
# linked in for the duration of the run when the worktree has none. A branch
# that changes the dependencies themselves must bring its own, since the main
# checkout's would not reflect the change. The backend suite runs through
# scripts/run_full_ci.sh, which starts a Postgres + ClickHouse pair of its own
# on free ports, so concurrent pushes from several sessions never share a
# database. Not covered locally: CI's frontend coverage thresholds (this gate
# runs `npm run test`, not `test:coverage`) and the Docker image build.
#
# Reads the tool input JSON on stdin; exit 2 = block the tool call.
set -uo pipefail
input="$(cat)"

# Cheap pre-filter on the raw JSON before paying for a python3 spawn. The JSON
# carries the command text verbatim, so this holds for every push whose
# subcommand is written with its letters together, quoted or not. Known gap,
# shared by any substring check: a spelling that splits the word with quotes
# or an escape (pu''sh, pu\sh) still pushes and is never gated.
printf '%s' "$input" | grep -q 'push' || exit 0

# The parser decides whether the command pushes and reports the push's own
# `-C`, refs and any preceding `cd` for the directory resolution below. It
# tokenises with shlex, which applies the shell's quoting rules, because a
# regex over the raw text cannot tell where a quoted value ends or which `git`
# invocation a flag belongs to. Over-matching costs an unneeded gate run, or a
# block for a command it cannot tokenise, while under-matching skips every
# check, so wherever the two trade off the parser over-matches.
PARSED="$(
  printf '%s' "$input" | python3 -c '
import json, shlex, sys

# Runs of these become tokens of their own, even glued to a word, so x;git push
# and (git push) each yield a statement of their own.
OPERATOR_CHARS = "();<>|&`"
# A token made only of these ends a statement: ;  &&  ||  |  &, a subshell or
# substitution bracket, a backtick, or a run of them such as ");".
SEPARATOR_CHARS = frozenset("();|&`")
# Global options git reads a separate value for, ahead of the subcommand.
GIT_VALUE_OPTIONS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--attr-source",
}


def newlines_as_separators(text):
    """An unquoted newline ends a statement exactly like ";", which shlex
    treats as plain whitespace. A newline inside quotes (a multi-line commit
    message) is content and stays, and an unquoted backslash-newline is a
    line continuation, so both characters vanish. chr(34) and chr(39) stand
    in for the quote characters because this script is itself a single-quoted
    shell argument."""
    dq, sq = chr(34), chr(39)
    quote, chars = None, []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            chars.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == dq and i + 1 < n:
                i += 1
                chars.append(text[i])
        elif ch == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 1
        elif ch == "\\" and i + 1 < n:
            chars.append(ch)
            i += 1
            chars.append(text[i])
        elif ch in (sq, dq):
            quote = ch; chars.append(ch)
        elif ch == "\n":
            chars.append(" ; ")
        else:
            chars.append(ch)
        i += 1
    return "".join(chars)


def shell_tokens(text):
    lexer = shlex.shlex(newlines_as_separators(text), posix=True, punctuation_chars=OPERATOR_CHARS)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def argument_tokens(text):
    """Tokens of a quoted argument parsed again as a command. Such an argument
    is often prose, where an apostrophe is ordinary, so a lexing failure here
    falls back to whitespace splitting instead of leaving the whole command
    without a verdict."""
    try:
        return shell_tokens(text)
    except ValueError:
        return text.split()


def statements(tokens):
    result, current = [], []
    for token in tokens:
        if token and set(token) <= SEPARATOR_CHARS:
            if current:
                result.append(current)
            current = []
        else:
            current.append(token)
    if current:
        result.append(current)
    return result


def git_push(statement):
    """(directory from -C, arguments after push) for a git push in the
    statement, else None. Every token naming git counts wherever it sits, so
    a wrapper in front of it (time, env, sudo, if) does not hide the push."""
    for start, token in enumerate(statement):
        if token.rsplit("/", 1)[-1] != "git":
            continue
        i, directory = start + 1, None
        while i < len(statement) and statement[i].startswith("-"):
            if statement[i] in GIT_VALUE_OPTIONS and i + 1 < len(statement):
                if statement[i] == "-C":
                    directory = statement[i + 1]
                i += 2
            else:
                i += 1
        if i < len(statement) and statement[i] == "push":
            return directory, statement[i + 1 :]
    return None


def find_push(tokens, cd_dir):
    """(cd_dir, directory, arguments) of the first push, else None. A push in
    a statement of its own outranks one inside an argument. Recursion ends
    because an argument holding whitespace is always shorter than the text it
    was lexed from: its quotes or escapes are gone."""
    nested = None
    for statement in statements(tokens):
        found = git_push(statement)
        if found:
            return (cd_dir, *found)
        for token in statement:
            if nested is None and any(c.isspace() for c in token):
                nested = find_push(argument_tokens(token), cd_dir)
        # A later cd overrides an earlier one, as it would in the shell.
        if statement[:1] == ["cd"] and len(statement) > 1:
            cd_dir = statement[1]
    return nested


def could_push(text):
    """Whether a command shlex cannot tokenise could still be a git push: push
    and git each appear as a word of their own, with quotes, escapes and
    operator characters taken as word breaks. Prose with an apostrophe (I will
    push later) or a path holding the letters (push-gate) then does not block."""
    breaks = str.maketrans({c: " " for c in OPERATOR_CHARS + chr(34) + chr(39) + "\\"})
    words = text.translate(breaks).split()
    return "push" in words and any(w.rsplit("/", 1)[-1] == "git" for w in words)


out = {"is_push": None, "dir": "", "cd_dir": "", "refs": [], "is_delete": False}
try:
    raw_cmd = str(json.load(sys.stdin).get("tool_input", {}).get("command") or "")
    tokens = []
    if "push" in raw_cmd:
        try:
            tokens = shell_tokens(raw_cmd)
        except ValueError:
            if could_push(raw_cmd):
                raise
    found = find_push(tokens, "")
    out["is_push"] = found is not None
    if found:
        out["cd_dir"], directory, args = found
        out["dir"] = directory or ""
        unforced = [a[1:] if a.startswith("+") else a for a in args]
        out["is_delete"] = any(a in ("--delete", "-d") for a in args) or any(
            a.startswith(":") and len(a) > 1 for a in unforced
        )
        out["refs"] = [a for a in args if not a.startswith("-") and a != "origin"]
except Exception:
    out["is_push"] = None
print(json.dumps(out))
' 2>/dev/null
)"
read_parsed() {
  printf '%s' "$PARSED" | python3 -c "import json,sys; v=json.load(sys.stdin).get('$1'); print('' if v is None else (' '.join(v) if isinstance(v, list) else v))" 2>/dev/null
}

# Only a literal false lets the command through. A command that could be a git
# push but that shlex cannot tokenise (an unbalanced quote, typically an
# apostrophe in a heredoc body) gets a null verdict, as does an empty or
# unreadable parse: its directory and refs are unknown, so nothing could be
# checked, and it blocks. The verdict is read from the JSON prefix (is_push is
# the first key json.dumps emits), sparing a python3 spawn on the common
# not-a-push path.
case "$PARSED" in
  '{"is_push": false, '*) exit 0 ;;
  '{"is_push": true, '*) ;;
  *)
    echo "BLOCKED: git push — could not parse the push command, so this gate cannot tell which branch" >&2
    echo "  it pushes and would check nothing. An unbalanced quote is the usual cause, such as an" >&2
    echo "  apostrophe in a heredoc body or a comment. Run the push as a Bash call of its own, and pass" >&2
    echo "  multi-line text through a file (git commit -F <file>) rather than a heredoc." >&2
    exit 2
    ;;
esac

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

# The local branch a refspec pushes: its source half (the destination half is
# a remote-side name), without the `+` that only forces the update or a
# `refs/heads/` prefix.
refspec_branch() {
  local branch="${1%%:*}"
  branch="${branch#+}"
  printf '%s' "${branch#refs/heads/}"
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
    echo "  written in a form it failed to read; pass it as a literal absolute path." >&2
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
    branch="$(refspec_branch "$ref")"
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
# parsing this gate got wrong — a spelling it does not cover (a variable, a
# relative path) reduced to something meaningless — and silently continuing
# from there would check the wrong tree, so it is reported instead. A path
# that does exist is simply not this repository's push, and gating someone
# else's repository is not this hook's business.
if [ -z "$GATE_DIR" ] && [ -n "$CD_DIR" ]; then
  if [ -e "$CD_DIR" ]; then
    exit 0
  fi
  echo "BLOCKED: git push — the command names directory '$CD_DIR', which does not exist, so" >&2
  echo "  this gate cannot tell which branch's files to check. That usually means the path was" >&2
  echo "  written in a form it failed to read; pass it as a literal absolute path." >&2
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

# poetry resolves its virtualenv by cwd identity, so a bare `poetry run` from a
# worktree picks up that worktree's own — usually unprovisioned — environment.
# The file-scoped ruff steps therefore run from $CLAUDE_PROJECT_DIR with the
# changed files passed as absolute paths under $GATE_DIR (ruff reads its
# configuration from each file's own nearest pyproject.toml); every
# whole-project step below runs inside $GATE_DIR with the virtualenv chosen
# by prepare_python_env.
cd "$CLAUDE_PROJECT_DIR" || { echo "BLOCKED: git push — could not cd to \$CLAUDE_PROJECT_DIR ($CLAUDE_PROJECT_DIR)." >&2; exit 2; }
if [ "$GATE_DIR" != "$CLAUDE_PROJECT_DIR" ]; then
  echo "== push gate: files from $GATE_DIR ==" >&2
fi

# A branch every one of whose commits suppresses CI produces no run at all, so
# the PR has nothing for the merge gate to read. Only the tip of the push is
# consulted, which is the part that is easy to get wrong: a trailer-less commit
# buried earlier in the branch changes nothing. A warning, not a block: the
# policy lives in AGENTS.md (a branch tip must not carry the trailer), and this
# only reports a tip that breaks it.
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
    echo "  A branch tip must not carry that trailer (see AGENTS.md, Git and pull" >&2
    echo "  requests): the merge gate needs a green run to read." >&2
    ;;
esac

LOG="$(mktemp)"
# One line per step that hit its ceiling. Kept apart from $LOG because a
# timeout prints no error of its own and the BLOCKED tail of $LOG is
# whatever a later step printed last, so the gate would otherwise block
# without naming the step that actually ran out of time.
TIMED_OUT="$(mktemp)"
# run_with_timeout's fallback path (below) creates a marker temp file per
# call; if this script's process receives a catchable termination (an
# external harness timeout, SIGTERM, or a normal early exit) after a marker
# is created but before that call's own cleanup runs, it would otherwise
# leak. Accumulate every marker path here so the EXIT trap sweeps them up
# alongside $LOG. (A literal SIGKILL still leaks the marker regardless --
# no shell trap, including EXIT, can catch it; same pre-existing limitation
# $LOG's own cleanup already had.)
MARKER_FILES=()
# One "<step name><TAB><that step's own output file>" line per step that
# failed, so a block can show each failure's own output instead of whatever
# a later, passing step printed last.
FAILED_STEPS="$(mktemp)"
STEP_DIR="$(mktemp -d)"
SHIM_DIR=""
LINKED_NODE_MODULES=""
cleanup() {
  rm -f "$LOG" "$TIMED_OUT" "$FAILED_STEPS"
  rm -rf "$STEP_DIR"
  [ -z "$SHIM_DIR" ] || rm -rf "$SHIM_DIR"
  # Only ever a symlink this script created; `rm -f` removes the link, never
  # the main checkout's node_modules it points at.
  [ -z "$LINKED_NODE_MODULES" ] || rm -f "$LINKED_NODE_MODULES"
  # Count-guarded: bash 3.2 (macOS's default /bin/bash -- exactly the
  # environment run_with_timeout's fallback branch targets) treats
  # "${MARKER_FILES[@]}" on an empty array as an unbound-variable error
  # under `set -u`.
  [ "${#MARKER_FILES[@]}" -eq 0 ] || rm -f "${MARKER_FILES[@]}"
}
trap cleanup EXIT
FAIL=0

# Neither GNU timeout nor gtimeout is on PATH (e.g. macOS without Homebrew
# coreutils). Running unbounded there would silently defeat this script's
# documented fail-closed contract, so run_with_timeout enforces the budget
# with this background watchdog instead. Every real call site is a compound
# command (bash -c "... && ..."), so job control (`set -m`) is required to
# put it in its own process group — otherwise TERM/KILL only hits the
# wrapper shell and leaves its child process tree (npm/vite/pytest workers)
# running as an orphan.
_run_with_watchdog() {
  local secs="$1"; shift
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

run_with_timeout() {
  local secs="$1"; shift
  local status
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
    status=$?
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
    status=$?
  else
    _run_with_watchdog "$secs" "$@"
    status=$?
  fi
  if [ "$status" -eq 124 ]; then
    printf 'TIMED OUT after %ss: %s\n' "$secs" "$*" >>"$TIMED_OUT"
  fi
  return "$status"
}

note_failed() {
  printf '%s\t%s\n' "$1" "$2" >>"$FAILED_STEPS"
}

# Runs one check with its output captured on its own, then appended to $LOG,
# so a failure is reported with the failing step's output. A timeout is left
# to run_with_timeout's own record rather than counted as a failure here.
run_step() {
  local secs="$1" name="$2"; shift 2
  local out; out="$(mktemp "$STEP_DIR/step.XXXXXX")"
  echo "== $name =="
  run_with_timeout "$secs" "$@" >"$out" 2>&1
  local status=$?
  cat "$out"
  if [ "$status" -ne 0 ] && [ "$status" -ne 124 ]; then
    note_failed "$name" "$out"
  fi
  return "$status"
}

# `timeout` (and the watchdog) run an external command, not a shell function,
# so a step that needs another working directory goes through bash, with the
# directory passed as an argument rather than spliced into the script text.
IN_DIR=(bash -c 'cd "$1" && shift && exec "$@"' in-dir)

block_with_log() {
  echo "BLOCKED: git push — $1." >&2
  if [ -s "${FAILED_STEPS:-}" ]; then
    local name out
    while IFS="$(printf '\t')" read -r name out; do
      echo "--- failed: $name (last 40 lines of its own output) ---" >&2
      tail -40 "$out" >&2
    done <"$FAILED_STEPS"
  else
    echo "Last 80 lines:" >&2
    tail -80 "$LOG" >&2
  fi
  if [ -s "$TIMED_OUT" ]; then
    echo "Ran out of time (the step's ceiling in this script, not a check failure):" >&2
    cat "$TIMED_OUT" >&2
  fi
  exit 2
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
  'frontend/*.cjs' 'frontend/*.mts' 'frontend/*.py'
  'frontend/*.json' 'frontend/*.html' 'frontend/*.css' 'tests/frontend/*.mjs'
)
# Paths that are not Python but carry backend pytest coverage (migration drift,
# Makefile and compose safety, the hooks' own tests), so changing one runs the
# backend checks.
BACKEND_PATHSPEC=(
  '*.sql' '*.sh' '*Makefile' '*compose*.yml' '.claude/hooks/*' '.codex/*'
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

# Whether the pushed branch changes any of the given paths. An unresolvable
# base counts as a change: it is the stricter answer, and a dependency change
# read as "none" would test the branch with tools that don't match it.
branch_changes() {
  [ "$SCOPE_OK" -eq 0 ] || [ -n "$(git -C "$GATE_DIR" diff --name-only "$BASE_REF"...HEAD -- "$@")" ]
}

# A dependency change touches no .py file, yet it can break every import, so
# it triggers the backend checks on its own.
DEPS_PATHSPEC=('pyproject.toml' 'poetry.lock')
PY_DEPS_CHANGED=0
if branch_changes "${DEPS_PATHSPEC[@]}"; then
  PY_DEPS_CHANGED=1
fi

BACKEND_PATHS_CHANGED=0
if [ "$SCOPE_OK" -eq 1 ] && branch_changes "${BACKEND_PATHSPEC[@]}"; then
  BACKEND_PATHS_CHANGED=1
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
if [ "$IS_DELETE" -eq 0 ] && [ "$SCOPE_OK" -eq 1 ] && [ "${#PY_FILES[@]}" -eq 0 ] && [ "${#FE_FILES[@]}" -eq 0 ] && [ "$PY_DEPS_CHANGED" -eq 0 ] && [ "$BACKEND_PATHS_CHANGED" -eq 0 ]; then
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
    branch="$(refspec_branch "$ref")"
    git rev-parse --verify --quiet "refs/heads/$branch" >/dev/null 2>&1 || continue
    # Filtered to the same pathspecs the scoped checks use (and with the
    # same --diff-filter=ACMR as PY_FILES/FE_FILES above): a branch whose
    # only change is a deletion, or only Markdown, legitimately produces no
    # files here, and comparing against its unfiltered diff would refuse
    # both pushes for a directory that was never actually wrong.
    if [ -n "$(git diff --name-only --diff-filter=ACMR "$BASE_REF...refs/heads/$branch" -- "${PY_PATHSPEC[@]}" "${FE_PATHSPEC[@]}" "${DEPS_PATHSPEC[@]}" "${BACKEND_PATHSPEC[@]}" 2>/dev/null)" ]; then
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
    run_step 60 "ruff format --check (changed files)" poetry run -- ruff format --check -- "${PY_FILES[@]}" || FAIL=1
    run_step 60 "ruff check (changed files)" poetry run -- ruff check -- "${PY_FILES[@]}" || FAIL=1
  } >>"$LOG" 2>&1
fi

RUN_BACKEND=0
if [ "$SCOPE_OK" -eq 0 ] || [ "${#PY_FILES[@]}" -gt 0 ] || [ "$PY_DEPS_CHANGED" -eq 1 ] || [ "$BACKEND_PATHS_CHANGED" -eq 1 ]; then
  RUN_BACKEND=1
fi

# Picks the virtualenv for the whole-project Python steps and writes a
# `poetry` shim that routes `poetry run <tool>` to it. Those steps run from
# $GATE_DIR, so the code under test is still the branch's: pytest's rootdir, a
# script's own directory and `python -c`'s cwd all precede the virtualenv's
# own path entry for the main checkout. A branch that changes the
# dependencies is tested in its own virtualenv instead. Returns non-zero when
# the Python steps are to be skipped by explicit opt-out.
prepare_python_env() {
  local venv
  if [ "$PY_DEPS_CHANGED" -eq 1 ] && [ "$GATE_DIR" != "$CLAUDE_PROJECT_DIR" ]; then
    venv="$(cd "$GATE_DIR" && poetry env info --path 2>/dev/null)"
    if [ -z "$venv" ] || [ ! -x "$venv/bin/pytest" ]; then
      if [ "${PUSH_GATE_SKIP_TESTS:-0}" = "1" ]; then
        echo "WARNING: this branch changes pyproject.toml/poetry.lock and $GATE_DIR has no provisioned virtualenv — PUSH_GATE_SKIP_TESTS=1 set, so mypy and the backend suite are left to CI (deliberate opt-out)." >&2
        return 1
      fi
      echo "BLOCKED: git push — this branch changes pyproject.toml/poetry.lock, which the main checkout's" >&2
      echo "  virtualenv does not reflect. Run 'poetry install' in $GATE_DIR so the gate can test" >&2
      echo "  against the new dependencies, or set PUSH_GATE_SKIP_TESTS=1 in the Claude Code session environment" >&2
      echo "  (settings \`env\` or the launch environment; a prefix on the push command does not reach this hook) to leave them to CI." >&2
      exit 2
    fi
  else
    venv="$(poetry env info --path 2>/dev/null)"
    if [ -z "$venv" ] || [ ! -x "$venv/bin/pytest" ]; then
      echo "BLOCKED: git push — $CLAUDE_PROJECT_DIR has no provisioned virtualenv; run 'poetry install' there." >&2
      exit 2
    fi
  fi
  SHIM_DIR="$(mktemp -d)"
  printf '#!/usr/bin/env bash\n[ "$1" = run ] || exec %q "$@"\nshift\n[ "$1" = -- ] && shift\ntool="$1"\nshift\nexec %q/bin/"$tool" "$@"\n' \
    "$(command -v poetry)" "$venv" >"$SHIM_DIR/poetry"
  chmod +x "$SHIM_DIR/poetry"
}
if [ "$RUN_BACKEND" -eq 1 ] && ! prepare_python_env; then
  RUN_BACKEND=0
fi

# mypy runs whole-project, not file-scoped: a type error is a property of a
# module and of everything importing it, so checking only the changed files
# would miss the breakage a changed signature causes in its callers.
if [ "$RUN_BACKEND" -eq 1 ]; then
  {
    run_step 180 "mypy (whole configured scope)" \
      "${IN_DIR[@]}" "$GATE_DIR" env PATH="$SHIM_DIR:$PATH" poetry run -- mypy || FAIL=1
  } >>"$LOG" 2>&1
fi

# Fail fast on the cheap checks before paying for the full backend + frontend
# suites — a one-line format nit shouldn't cost a multi-minute double run.
if [ "$FAIL" -ne 0 ]; then
  block_with_log "ruff format/lint or mypy failed (skipping tests)"
fi

# The backend suite runs through scripts/run_full_ci.sh rather than against
# the long-lived :5544/:8124 pair: that pair is shared by every session on
# the host, and two suites running against it at once reset each other's
# tables mid-test. run_full_ci.sh starts a pair of its own on free ports and
# removes it afterwards. If Docker is unreachable that's a failed check, not
# a skip — PUSH_GATE_SKIP_TESTS=1 is the explicit, visible opt-out.
if [ "$RUN_BACKEND" -eq 1 ]; then
  if [ "${PUSH_GATE_SKIP_TESTS:-0}" = "1" ]; then
    echo "WARNING: PUSH_GATE_SKIP_TESTS=1 set — skipping the backend suite for this push (deliberate opt-out; CI still runs it)." >&2
  elif ! run_with_timeout 30 docker info >/dev/null 2>&1; then
    echo "Docker is not reachable, so the backend suite's own Postgres + ClickHouse cannot start. Start Docker, or set PUSH_GATE_SKIP_TESTS=1 in the Claude Code session environment (not as a prefix on the push command) to skip explicitly (not recommended)." >"$STEP_DIR/docker"
    note_failed "backend suite" "$STEP_DIR/docker"
    FAIL=1
  else
    # The whole backend suite is this gate's long pole by an order of
    # magnitude, so the ceiling has to clear the suite's real wall-clock on a
    # loaded host with room for it to keep growing. Set too tight, the
    # timeout fires on every Python change and the gate never reports a
    # genuine pass -- pushes then either look broken or get routed around,
    # which is strictly worse than no gate.
    #
    # Every ceiling in this script is bounded by one more: the `timeout` on
    # this hook's entry in .claude/settings.json, enforced by the harness
    # rather than by this script. If the ceilings here can sum past it, the
    # harness kills the script before it reaches its own exit, and whether
    # that blocks the push or lets it through is outside this script's
    # control -- the one outcome its fail-closed design cannot guarantee.
    # Raise that entry alongside any ceiling raised here.
    run_step 1500 "backend suite (scripts/run_full_ci.sh: own Postgres + ClickHouse on free ports)" \
      "${IN_DIR[@]}" "$GATE_DIR" env PATH="$SHIM_DIR:$PATH" GEMINI_API_KEY=test-key scripts/run_full_ci.sh -x -q \
      >>"$LOG" 2>&1 || FAIL=1
  fi
fi

FE_DIR="$GATE_DIR/frontend"
RUN_FRONTEND=0
if { [ "$SCOPE_OK" -eq 0 ] || [ "${#FE_FILES[@]}" -gt 0 ]; } && [ -d "$FE_DIR" ]; then
  RUN_FRONTEND=1
fi

# Uses $FE_DIR's own node_modules when it has a real one. Otherwise the main
# checkout's is linked in for this run and removed again on exit -- unless
# the branch changes the frontend's dependencies, which that copy would not
# reflect.
prepare_node_modules() {
  local fe_deps_changed=0
  if branch_changes frontend/package.json frontend/package-lock.json; then
    fe_deps_changed=1
  fi
  if [ -e "$FE_DIR/node_modules" ] && { [ ! -L "$FE_DIR/node_modules" ] || [ "$fe_deps_changed" -eq 0 ]; }; then
    return 0
  fi
  if [ "$fe_deps_changed" -eq 1 ]; then
    echo "BLOCKED: git push — this branch changes frontend/package.json or package-lock.json, which the main" >&2
    echo "  checkout's node_modules does not reflect. Run 'npm ci' in $FE_DIR, then push again." >&2
    exit 2
  fi
  # -fn replaces a dangling link a hard-killed earlier run could not remove;
  # a real directory never reaches here (the early return above keeps it).
  if [ ! -d "$CLAUDE_PROJECT_DIR/frontend/node_modules" ] || ! ln -sfn "$CLAUDE_PROJECT_DIR/frontend/node_modules" "$FE_DIR/node_modules"; then
    echo "BLOCKED: git push — $FE_DIR has no node_modules and the main checkout's could not be linked in" >&2
    echo "  (run 'npm ci' in either)." >&2
    exit 2
  fi
  LINKED_NODE_MODULES="$FE_DIR/node_modules"
}

if [ "$RUN_FRONTEND" -eq 1 ]; then
  prepare_node_modules
  {
    # --force: tsc -b decides a project is up to date from timestamps, and a
    # node_modules linked in from the main checkout carries build info that
    # other worktrees' runs wrote, so an unforced run could skip the check.
    run_step 90 "npm run typecheck (whole project — tsc project refs can't be file-scoped)" \
      "${IN_DIR[@]}" "$FE_DIR" npm run typecheck -- --force || FAIL=1
    # Whole-project vitest saturates every core and grows with each test
    # file, so like the backend ceiling above it needs headroom over the
    # suite's real wall-clock on a loaded host, not a figure that only a
    # quiet machine clears.
    run_step 300 "npm run test" "${IN_DIR[@]}" "$FE_DIR" npm run test || FAIL=1
    # Type-aware lint over the whole project; same loaded-host headroom
    # reasoning as the test ceiling above.
    run_step 180 "npm run lint (whole project — matches CI, a file-scoped eslint call can miss project config)" \
      "${IN_DIR[@]}" "$FE_DIR" npm run lint || FAIL=1
    run_step 30 "npm run lint:i18n" "${IN_DIR[@]}" "$FE_DIR" npm run lint:i18n || FAIL=1
    run_step 30 "npm run lint:i18n-strings" "${IN_DIR[@]}" "$FE_DIR" npm run lint:i18n-strings || FAIL=1
    echo "== npm run deadcode (knip, JSON reporter; same analysis CI's dead-code gate runs) =="
    # Two different failures both come out of `knip` as a non-zero exit: dead
    # code, which must block, and a toolchain that cannot run knip at all,
    # which must not. The second is common rather than exotic -- knip needs a
    # newer Node than an ambient install often provides, and it parses through
    # oxc-parser's platform-specific native binary, which npm's
    # optional-dependency resolution drops often enough to expect (npm/cli#4828).
    # Every push in the repository runs this gate, so a broken local toolchain
    # would block all of them behind a stack trace that names neither the
    # cause nor the cure.
    #
    # The two are told apart structurally rather than by matching error text:
    # knip that ran writes a report, and knip that could not start writes
    # nothing parseable. Hence the JSON reporter -- the analysis is the one CI
    # runs, only the output shape differs, and that shape is what makes the
    # distinction decidable.
    deadcode_out="$(mktemp "$STEP_DIR/deadcode.XXXXXX")"
    deadcode_err="$(mktemp "$STEP_DIR/deadcode-err.XXXXXX")"
    run_with_timeout 90 "${IN_DIR[@]}" "$FE_DIR" npm run --silent deadcode -- --reporter json \
      >"$deadcode_out" 2>"$deadcode_err"
    rc=$?
    cat "$deadcode_out" "$deadcode_err"
    if [ "$rc" -eq 124 ]; then
      # A check that never finished is not the narrow exception below: nothing
      # was classified, so the file header's fail-closed contract applies.
      echo "npm run deadcode TIMED OUT after 90s — nothing to classify, so this blocks." >&2
      FAIL=1
    elif [ "$rc" -ne 0 ]; then
      if node -e 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))' "$deadcode_out" 2>/dev/null; then
        note_failed "npm run deadcode" "$deadcode_out"
        FAIL=1
      else
        echo "WARNING: npm run deadcode produced no report — this toolchain cannot run knip (check node's version against knip's \`engines\`, that oxc-parser's native binding is installed in $FE_DIR/node_modules, and that frontend/package.json still defines a \`deadcode\` script). The local dead-code gate is skipped for this push; CI enforces it either way." >&2
        # `--silent` is what keeps npm's banner out of the JSON, and it takes
        # npm's own error output with it, so an npm-level failure would
        # otherwise leave the log with no reason in it at all. One plain
        # re-run costs nothing on a toolchain that is already failing fast.
        echo "== npm run deadcode (plain re-run; the JSON run above produced no report) =="
        run_with_timeout 90 "${IN_DIR[@]}" "$FE_DIR" npm run deadcode || true
      fi
    fi
    run_step 30 "npm run test:check-entry-chunk (fixture-based positive/negative controls for the checker itself)" \
      "${IN_DIR[@]}" "$FE_DIR" npm run test:check-entry-chunk || FAIL=1
    run_step 30 "npm run test:check-css-tokens (fixture-based positive/negative controls for the checker itself)" \
      "${IN_DIR[@]}" "$FE_DIR" npm run test:check-css-tokens || FAIL=1
    run_step 30 "npm run test:check-react-compiler (fixture-based positive/negative controls for the checker itself)" \
      "${IN_DIR[@]}" "$FE_DIR" npm run test:check-react-compiler || FAIL=1
    run_step 120 "npm run check:react-compiler (the React Compiler compiles every component and hook)" \
      "${IN_DIR[@]}" "$FE_DIR" npm run check:react-compiler || FAIL=1
    run_step 30 "npm run check:css-tokens (static scan: var(--x) refs resolve, z-index uses the shared ladder)" \
      "${IN_DIR[@]}" "$FE_DIR" npm run check:css-tokens || FAIL=1
    if [ "${PUSH_GATE_SKIP_BUILD:-0}" = "1" ]; then
      echo "WARNING: PUSH_GATE_SKIP_BUILD=1 set — skipping npm run build:bundle + check:entry-chunk for this push (deliberate opt-out; MapLibre-in-entry regressions won't be caught locally)." >&2
    else
      run_step 480 "npm run build:bundle && npm run check:entry-chunk (MapLibre must stay out of the entry chunk; typecheck already ran above, so this build step skips tsc -b)" \
        "${IN_DIR[@]}" "$FE_DIR" bash -c 'npm run build:bundle && npm run check:entry-chunk'
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
  block_with_log "quality gate failed"
fi

exit 0
