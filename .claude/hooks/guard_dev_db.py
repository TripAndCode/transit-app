"""PreToolUse(Bash) hook body: block write/DDL SQL aimed at the dev DB on :5433.

Reads the tool input JSON on stdin; exit 2 = block the tool call, 0 = allow.

Targeting is decided on shlex-tokenized argv, not on a substring match over the
raw command. A regex over raw text only recognises the one spelling it was
written for, so `-h localhost -p 5433`, an inserted `docker compose
--project-name x exec db`, or `compose run` in place of `compose exec` all reach
the dev database untouched while looking like they are covered.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

DEV_PORT = "5433"
DEV_SERVICES = {"db", "clickhouse"}
# Throwaway stacks. Naming one is the signal that a command was pointed away
# from the dev database on purpose.
TEST_PORTS = (":5544", ":8124")
# Make targets that take the Makefile's own DATABASE_URL default -- the real
# dev database -- when the caller overrides nothing. They name no host, port or
# container, so nothing else here can recognise what they are aimed at.
DESTRUCTIVE_TARGETS = {"migrate-down", "db-reset"}

# A mutation the dev database must never take. Matched over the raw command:
# this asks "does this text contain a mutating statement", which needs no shell
# structure, unlike deciding what the command is aimed at.
WRITE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b|db-reset|migrate[^ ]*down|downgrade",
    re.IGNORECASE,
)

# Compose derives container names as `<project>-<service>-N`, and the project
# defaults to the checkout's directory name -- so match the shape, not one
# literal project. `transit-pg` is the older pinned name, still carried by any
# container created before compose.yml dropped `container_name`.
CONTAINER = re.compile(r"[a-z0-9_.-]*-db-\d+")


def targets_dev_db(tokens: list[str], cmd: str) -> bool:
    lowered = [t.lower() for t in tokens]

    # A destructive Make target inherits the dev database unless the caller
    # pointed it somewhere throwaway in the same command.
    if "make" in lowered and DESTRUCTIVE_TARGETS & set(lowered):
        if not any(port in cmd for port in TEST_PORTS):
            return True

    for i, tok in enumerate(lowered):
        if f":{DEV_PORT}" in tok:
            return True
        if tok in ("-p", "--port") and i + 1 < len(lowered) and lowered[i + 1] == DEV_PORT:
            return True
        if tok in (f"-p{DEV_PORT}", f"--port={DEV_PORT}", f"pgport={DEV_PORT}"):
            return True
        if tok == "transit-pg" or CONTAINER.fullmatch(tok):
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
    return targets_dev_db(tokens, cmd) and bool(WRITE.search(cmd))


def main() -> int:
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "") or ""
    except Exception:
        return 0
    if should_block(cmd):
        sys.stderr.write(
            "BLOCKED: write/DDL SQL against dev DB :5433 (read-only, real production data). "
            "Use the :5544 test DB. See CLAUDE.md.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
