"""PreToolUse(Bash) hook body: refuse the git commands that damage a shared clone.

Several sessions, each in its own worktree, share one clone: one stash stack, one
`main`, one remote. AGENTS.md forbids three commands outright, and this hook refuses
them so a session cannot run one by mistake:

- a push that lands on `main`: a `main` destination in any refspec (`main`,
  `HEAD:main`, `feature:refs/heads/main`, `:main`, `--delete origin main`),
  `--all`/`--mirror`/`--branches`, or a push of the current branch (`HEAD`, `@`, or
  no refspec at all) while `main` is the checked-out branch of the directory the push
  runs in. A tags-only push (`--tags` without `--follow-tags`) moves no branch and
  passes there;
- a force-push spelled `--force`, `-f` or `+refspec`, which can drop commits another
  session pushed. `--force-with-lease` on its own is the sanctioned spelling and
  passes; `--force` written beside it is refused all the same, so the lease form is
  the only one a session learns to write;
- `git stash pop`, `drop` or `clear`, which rewrite the one stash stack every worktree
  sees. `stash apply`, `list`, `show`, `branch` and `push` leave the other entries
  alone and pass.

Reads the tool input JSON on stdin; exit 2 = block, 0 = allow. An unreadable payload
is refused, as the dev-store guard does.

Decisions are made on shlex-tokenized statements, so a `git` reached through `-C`, a
global option, a wrapper (`time`, `env`), `&&`/`;`/`|` or a newline, a shell `-c`
script or `eval` is read the same way, while the words inside a commit message, a grep
pattern or an echo are prose and pass. A command shlex cannot tokenize (an unbalanced
quote, usually an apostrophe) is read line by line for the same shapes instead, each
line cut at its first separator or `#`.

Known gaps, still allowed: a destination that is computed (`git push origin
"$BRANCH"`) or written as `heads/main` rather than `main` or `refs/heads/main`, a
script runner outside sh/bash/zsh/dash/ksh `-c` and `eval`, a `cd` whose target is
computed, bare or `-` (home and the previous directory), or inside a subshell, and
a git alias. A heredoc body line is read as a statement
like any other, so prose there can block instead; put such text in a file. A crash
inside this module blocks rather than allows, since an unchecked command is the
failure this hook exists to prevent.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED = {"main", "refs/heads/main"}
# Refspec sources that name whatever branch is checked out.
CURRENT_BRANCH = {"HEAD", "@"}
# Global git options that take their value as the next token, ahead of the subcommand.
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--attr-source"}
# Push options that take their value as the next token.
PUSH_VALUE_OPTIONS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
# git accepts any unambiguous prefix of a long option (`--mirr`), so the ones this
# hook reads are resolved against the full list rather than matched literally.
PUSH_LONG_OPTIONS = (
    "--all",
    "--atomic",
    "--branches",
    "--delete",
    "--dry-run",
    "--exec",
    "--follow-tags",
    "--force",
    "--force-if-includes",
    "--force-with-lease",
    "--ipv4",
    "--ipv6",
    "--mirror",
    "--no-verify",
    "--porcelain",
    "--progress",
    "--prune",
    "--push-option",
    "--quiet",
    "--receive-pack",
    "--recurse-submodules",
    "--repo",
    "--set-upstream",
    "--signed",
    "--tags",
    "--thin",
    "--verbose",
    "--verify",
)
# Stash options that take their value as the next token, so the value is not read as
# the stash subcommand (`git stash -m pop` saves with the message "pop").
STASH_VALUE_OPTIONS = {"-m", "--message"}
STASH_REWRITES = {"pop", "drop", "clear"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
OPERATOR_CHARS = "();<>|&\n"
# An operator run that ends a statement. A redirect run that carries `&` (`2>&1`)
# ends one too, which only shortens a statement that is scanned for every `git` in
# it anyway.
_STATEMENT_END = set(";|&()\n")

# For text shlex cannot tokenize: git, optional global options (with a value that is
# not itself the subcommand), then the subcommand.
_RAW_GIT = r"\bgit\b(?:\s+-\S+(?:\s+(?!push\b|stash\b)[^-\s]\S*)?)*"
RAW_STASH = re.compile(_RAW_GIT + r"\s+stash\s+(?:-\S+\s+)*(pop|drop|clear)\b")
RAW_PUSH = re.compile(_RAW_GIT + r"\s+push\b(.*)")
# Where a push's own arguments end on an unlexed line: the next statement or a comment.
RAW_ARGUMENTS_END = re.compile(r"\s*(?:;|&&|\|\||\||#).*$")


def shell_tokens(text: str) -> list[str]:
    """Shell words and operator runs. A newline ends a statement like `;` (shlex
    treats it as whitespace), so it is an operator here; one inside quotes stays
    content. `#` starts a comment, as in the shell, so a remark naming a forbidden
    command does not block."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=OPERATOR_CHARS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    return list(lexer)


def _ends_statement(token: str) -> bool:
    return bool(token) and set(token) <= set(OPERATOR_CHARS) and bool(set(token) & _STATEMENT_END)


def statements(tokens: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if _ends_statement(token):
            if current:
                out.append(current)
                current = []
        else:
            current.append(token)
    if current:
        out.append(current)
    return out


def _resolve(base: str, path: str) -> str:
    return os.path.join(base, os.path.expanduser(path))


def current_branch(directory: str) -> str | None:
    """The branch checked out in `directory`, or None when that cannot be read (no
    repository there, a detached HEAD, a missing directory)."""
    try:
        result = subprocess.run(
            ["git", "-C", directory, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def stash_verdict(args: list[str]) -> str | None:
    k = 0
    while k < len(args) and args[k].startswith("-"):
        k += 2 if args[k] in STASH_VALUE_OPTIONS else 1
    if k < len(args) and args[k] in STASH_REWRITES:
        return (
            f"git stash {args[k]} — the stash stack is shared by every worktree of this clone, and this rewrites it. "
            "Restore an entry with `git stash apply <sha>` and set work aside with a WIP commit instead (AGENTS.md)."
        )
    return None


def _long_option(name: str) -> str:
    """`name` resolved the way git resolves it: itself when it is a full option, else
    the one option it is an unambiguous prefix of, else unchanged."""
    if name in PUSH_LONG_OPTIONS:
        return name
    candidates = [option for option in PUSH_LONG_OPTIONS if option.startswith(name)]
    return candidates[0] if len(candidates) == 1 else name


def _push_shape(args: list[str]) -> tuple[bool, bool, bool, list[str]]:
    """(force, everything, tags_only, destinations) of a push's arguments. `force` is
    the unleased spelling only: `--force`, a short cluster holding `f`, or `+refspec`.
    `everything` covers `--all`/`--mirror`/`--branches` and the refspecs that push every
    branch without naming one: a `*` pattern and the bare matching `:`. With `--repo`
    the remote is not a positional, so every positional is a refspec."""
    force = everything = tags = follow_tags = repo_given = False
    positionals: list[str] = []
    k = 0
    while k < len(args):
        arg = args[k]
        if arg == "--":
            positionals.extend(args[k + 1 :])
            break
        if arg.startswith("--"):
            name = _long_option(arg.split("=", 1)[0])
            if name in PUSH_VALUE_OPTIONS and "=" not in arg:
                repo_given = repo_given or name == "--repo"
                k += 2
                continue
            if name == "--force":
                force = True
            elif name in ("--all", "--mirror", "--branches"):
                everything = True
            elif name == "--tags":
                tags = True
            elif name == "--follow-tags":
                follow_tags = True
            elif name == "--repo":
                repo_given = True
            k += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            # In a short cluster everything after `o` is that option's value, so only
            # the letters before it are flags; a bare trailing `o` takes the next token.
            flags, has_o, value = arg[1:].partition("o")
            if "f" in flags:
                force = True
            k += 2 if has_o and not value else 1
            continue
        positionals.append(arg)
        k += 1
    destinations = []
    for spec in positionals if repo_given else positionals[1:]:
        if spec.startswith("+"):
            force = True
            spec = spec[1:]
        destination = spec.split(":", 1)[1] if ":" in spec else spec
        if destination == "" or "*" in destination:
            everything = True
        destinations.append(destination)
    return force, everything, tags and not follow_tags, destinations


def _lands_on_main(detail: str) -> str:
    return (
        f"git push — this push lands on `main` ({detail}). `main` moves only by squash-merging a reviewed PR "
        "(AGENTS.md): push the branch and open a draft PR."
    )


_FORCE = (
    "git push — `--force`, `-f` or a `+refspec` can drop commits another session pushed. "
    "Write `git push --force-with-lease origin <branch>` instead, and only on a branch this session created "
    "(AGENTS.md)."
)


def push_verdict(args: list[str], where: str) -> str | None:
    force, everything, tags_only, destinations = _push_shape(args)
    if everything:
        return _lands_on_main("--all, --mirror, --branches, a `*` refspec or a bare `:` pushes every branch")
    for destination in destinations:
        if destination in PROTECTED:
            return _lands_on_main(f"destination `{destination}`")
    current = [d for d in destinations if d in CURRENT_BRANCH]
    pushes_checked_out_branch = bool(current) or (not destinations and not tags_only)
    if pushes_checked_out_branch and current_branch(where) == "main":
        how = f"`{current[0]}` names it" if current else "no refspec was given"
        return _lands_on_main(f"`main` is checked out in {where} and {how}")
    if force:
        return _FORCE
    return None


def git_verdict(statement: list[str], cwd: str) -> str | None:
    """Every token naming git counts wherever it sits, so a wrapper in front of it
    (`time`, `env`, `sudo`) or a second git on the same line does not hide it."""
    for i, token in enumerate(statement):
        if token.rsplit("/", 1)[-1] != "git":
            continue
        j, directory = i + 1, None
        while j < len(statement) and statement[j].startswith("-"):
            option = statement[j]
            if option in GIT_VALUE_OPTIONS:
                if option == "-C" and j + 1 < len(statement):
                    directory = statement[j + 1]
                j += 2
            else:
                if option.startswith("-C") and len(option) > 2:
                    directory = option[2:]
                j += 1
        if j >= len(statement):
            continue
        subcommand, args = statement[j], statement[j + 1 :]
        where = _resolve(cwd, directory) if directory else cwd
        reason = None
        if subcommand == "stash":
            reason = stash_verdict(args)
        elif subcommand == "push":
            reason = push_verdict(args, where)
        if reason:
            return reason
    return None


def nested_verdict(statement: list[str], cwd: str) -> str | None:
    """A script handed to a shell's `-c` (alone or in a cluster such as `-lc`) or to
    `eval` is a command line of its own."""
    for i, token in enumerate(statement):
        name = token.rsplit("/", 1)[-1]
        if name in SHELLS:
            for j in range(i + 1, len(statement) - 1):
                flag = statement[j]
                if not flag.startswith("-"):
                    break
                if not flag.startswith("--") and "c" in flag[1:]:
                    return verdict(statement[j + 1], cwd)
        elif name == "eval":
            return verdict(" ".join(statement[i + 1 :]), cwd)
    return None


def raw_verdict(command: str, cwd: str) -> str | None:
    for line in command.splitlines():
        stash = RAW_STASH.search(line)
        if stash:
            return stash_verdict([stash.group(1)])
        push = RAW_PUSH.search(line)
        if push:
            reason = push_verdict(RAW_ARGUMENTS_END.sub("", push.group(1)).split(), cwd)
            if reason:
                return reason
    return None


def verdict(command: str, cwd: str) -> str | None:
    """The reason to block `command`, or None. `cwd` is where it runs; a `cd` in an
    earlier statement moves it for the statements after."""
    try:
        tokens = shell_tokens(command)
    except ValueError:
        return raw_verdict(command, cwd)
    for statement in statements(tokens):
        reason = nested_verdict(statement, cwd) or git_verdict(statement, cwd)
        if reason:
            return reason
        if statement[0] == "cd" and len(statement) > 1 and not statement[1].startswith("-"):
            cwd = _resolve(cwd, statement[1])
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError("command is not a string")
        cwd = payload.get("cwd")
    except (ValueError, KeyError, TypeError, AttributeError):
        sys.stderr.write("BLOCKED: guard-git-shared could not read the tool input, so it cannot tell what this runs.\n")
        return 2
    reason = verdict(command, cwd if isinstance(cwd, str) and cwd else os.getcwd())
    if reason is None:
        return 0
    sys.stderr.write(f"BLOCKED: {reason}\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"BLOCKED: guard-git-shared failed before it could judge this command ({exc!r}).\n")
        sys.exit(2)
