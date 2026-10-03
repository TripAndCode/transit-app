"""PreToolUse(Bash) hook body: block writes aimed at either dev store.

Covers dev Postgres and dev ClickHouse. Both hold real production data and are
read-only for agents; the throwaway pair on :5544/:8124 is where writes belong.

Two Postgres ports, not one. `compose.yml` publishes :5433, but the container
actually holding the dev dataset can be published elsewhere -- :5543 today --
and the guard has to name every port the data is reachable on, not the one the
compose file happens to declare. A port listed here that turns out to hold
someone else's database is harmless: refusing to write to it is right either
way. A port left out is the dataset.

Reads the tool input JSON on stdin; exit 2 = block the tool call, 0 = allow.

Targeting is decided on shlex-tokenized argv, not on a substring match over the
raw command. A regex over raw text only recognises the one spelling it was
written for, so `-h localhost -p 5433`, an inserted `docker compose
--project-name x exec db`, or `compose run` in place of `compose exec` all reach
the dev database untouched while looking like they are covered.

Blocking takes both a dev target and a mutation, and nothing beyond that: a
command that merely names a dev store next to a write keyword is blocked even
when it is only searching text. That direction is deliberate — a false block
costs a rephrase, a missed write costs the dataset. The case that actually
bites is prose *about* this guard: a heredoc commit message naming a dev
container beside a keyword is a Bash command like any other. Put the text in a
file and pass the path, so it never reaches the command line.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

DEV_PORTS = ("5433", "5543", "8123")
DEV_SERVICES = {"db", "clickhouse"}
# Pinned container names, from before compose.yml dropped `container_name`.
# Still carried by any container created back then, and both are live today.
DEV_CONTAINERS = {"transit-pg", "transit-ch"}
# Throwaway stacks. Naming one is the signal that a command was pointed away
# from the dev stores on purpose.
TEST_PORTS = (":5544", ":8124")
# Make targets that take the Makefile's own DATABASE_URL default -- the real
# dev database -- when the caller overrides nothing. They name no host, port or
# container, so nothing else here can recognise what they are aimed at.
DESTRUCTIVE_TARGETS = {"migrate-down", "db-reset"}

# A mutation neither dev store must take. Matched over the raw command: this
# asks "does this text contain a mutating statement", which needs no shell
# structure, unlike deciding what the command is aimed at.
#
# The CLI names are spelled out because `\bCREATE\b` does not match inside
# `createdb` -- the boundary it needs isn't there. `VACUUM FULL` is listed but
# plain `VACUUM` is not: it takes no exclusive lock and mutates no rows.
# `\copy ... FROM` loads data in; `\copy ... TO` reads it out and stays allowed.
WRITE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|REINDEX)\b"
    r"|\b(dropdb|createdb|pg_restore)\b"
    r"|\bVACUUM\s+FULL\b"
    r"|\\copy\b[^|;&]*\bfrom\b"
    r"|db-reset|migrate[^ ]*down|downgrade",
    re.IGNORECASE,
)

# Compose derives container names as `<project>-<service>-N`, and the project
# defaults to the checkout's directory name -- so match the shape, not one
# literal project.
CONTAINER = re.compile(r"[a-z0-9_.-]*-(db|clickhouse)-\d+")


def runs_a_sql_script(lowered: list[str]) -> bool:
    """`psql -f file.sql` carries its statements in a file this hook can't read.

    Structural rather than textual: the flag is what makes the command a write,
    and nothing in the visible text says so.
    """
    return "psql" in lowered and ("-f" in lowered or "--file" in lowered)


def targets_dev_db(tokens: list[str], cmd: str) -> bool:
    lowered = [t.lower() for t in tokens]

    # A destructive Make target inherits the dev database unless the caller
    # pointed it somewhere throwaway in the same command.
    if "make" in lowered and DESTRUCTIVE_TARGETS & set(lowered):
        if not any(port in cmd for port in TEST_PORTS):
            return True

    for i, tok in enumerate(lowered):
        for port in DEV_PORTS:
            if f":{port}" in tok:
                return True
            if tok in ("-p", "--port") and i + 1 < len(lowered) and lowered[i + 1] == port:
                return True
            if tok in (f"-p{port}", f"--port={port}", f"pgport={port}"):
                return True
        if tok in DEV_CONTAINERS or CONTAINER.fullmatch(tok):
            return True

    # `docker compose exec|run <service>` reaches the same volume and network
    # either way, and global flags may sit anywhere between the words, so this
    # deliberately does not require them to be adjacent.
    return "compose" in lowered and bool({"exec", "run"} & set(lowered)) and bool(DEV_SERVICES & set(lowered))


def should_block(cmd: str) -> bool:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # Unbalanced quotes: fall back to whitespace splitting rather than give
        # up, so a malformed command can't slip past by failing to tokenize.
        tokens = cmd.split()
    if not targets_dev_db(tokens, cmd):
        return False
    return bool(WRITE.search(cmd)) or runs_a_sql_script([t.lower() for t in tokens])


def main() -> int:
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "") or ""
    except Exception:
        return 0
    if should_block(cmd):
        sys.stderr.write(
            "BLOCKED: write against a dev store (Postgres :5433/:5543 / ClickHouse :8123) — "
            "both hold real production data and are read-only. "
            "Use the throwaway :5544 / :8124 pair. See CLAUDE.md.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
