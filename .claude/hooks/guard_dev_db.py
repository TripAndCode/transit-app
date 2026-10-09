"""PreToolUse(Bash) hook body: block writes aimed at either dev store.

Covers dev Postgres and dev ClickHouse. Both hold real production data and are
read-only for agents; the throwaway pair on :5544/:8124 is where writes belong.

Two Postgres ports, not one. `compose.yml` publishes :5433, but the container
actually holding the dev dataset can be published elsewhere -- :5543 today --
and the guard has to name every port the data is reachable on, not the one the
compose file happens to declare. A port listed here that turns out to hold
someone else's database is harmless: refusing to write to it is right either
way. A port left out is the dataset.

Reads the tool input JSON on stdin; exit 2 = block the tool call (also when the
payload cannot be parsed), 0 = allow.

Targeting is decided on shlex-tokenized argv, not on a substring match over the
raw command. A regex over raw text only recognises the one spelling it was
written for, so `-h localhost -p 5433`, an inserted `docker compose
--project-name x exec db`, or `compose run` in place of `compose exec` all reach
the dev database untouched while looking like they are covered.

A command is blocked when it pairs a dev target with a mutation, when it tears
down a dev volume, or when it runs a destructive Make target or CLI subcommand
not pointed at the throwaway stack -- the last two need no SQL keyword. A
payload the hook cannot read is refused. A command that merely names a dev store next to a write keyword
is blocked even when it is only searching text. That direction is deliberate — a false block
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
# Container names compose did not derive: the pinned names from before
# compose.yml dropped `container_name`, and the dev Postgres recreated by hand
# on a newer major.
DEV_CONTAINERS = {"transit-pg", "transit-ch", "transit-pg-latest-main"}
# Throwaway stacks, per store. Naming one exempts a destructive command, which
# names no host of its own, from that store; it exempts nothing else.
# Matched against the lowercased NAME=value assignments of the same simple
# command -- the env prefix or a make variable -- so a throwaway port named
# anywhere else (another command, a comment) points nothing at it.
THROWAWAY = {
    "pg": re.compile(r"database_url=\S*:5544\b"),
    "ch": re.compile(r"clickhouse_port=8124\b"),
}
# Commands that write through DATABASE_URL and CLICKHOUSE_* when the caller
# overrides nothing -- the Makefile's own defaults from .env, the shell's for
# the CLI -- which are the real dev stores. They name no host, port or
# container, so nothing else here can recognise what they are aimed at, and
# running one is the write: no SQL keyword shows on the command line. Each maps
# to the stores it writes; it runs only when the command points every one of
# them at the throwaway stack.
DESTRUCTIVE_TARGETS = {
    "analyze": {"pg"},
    "analyze-all": {"pg"},
    "build-rag-index": {"pg"},
    "ch-bootstrap": {"ch"},
    "db": {"pg", "ch"},
    "fetch-ingest": {"pg", "ch"},
    "ingest": {"pg", "ch"},
    "ingest-weather": {"pg"},
    "load_static": {"pg"},
    "migrate": {"pg"},
    "migrate-down": {"pg"},
    "promote-intent-cache": {"pg"},
    "prune-admin-audit": {"pg"},
    "prune-personal-data": {"pg"},
    "prune-pipeline-runs": {"pg"},
    "prune-query-log": {"pg"},
    "seed-agencies": {"pg"},
}
DESTRUCTIVE_SUBCOMMANDS = {
    "add_agency": {"pg"},
    "analyze": {"pg"},
    "analyze_all": {"pg"},
    "build_rag_index": {"pg"},
    "ingest": {"pg", "ch"},
    "ingest_live": {"pg", "ch"},
    "ingest_weather": {"pg"},
    "load_static": {"pg"},
    "migrate": {"pg"},
    "prune-admin-audit": {"pg"},
    "prune-personal-data": {"pg"},
    "prune-pipeline-runs": {"pg"},
    "prune_query_log": {"pg"},
    "refresh-static": {"pg"},
    "restamp_archive": {"pg", "ch"},
    "seed_agencies": {"pg"},
}

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
    r"|migrate[\s_-]*down|downgrade",
    re.IGNORECASE,
)

# Compose derives container names as `<project>-<service>-N`, and the project
# defaults to the checkout's directory name -- so match the shape, not one
# literal project.
CONTAINER = re.compile(r"[a-z0-9_.-]*-(db|clickhouse)-\d+")
# compose.yml's named volumes, as compose publishes them: `<project>_<name>`.
# The hand-made dev Postgres container mounts the compose `transit_pgdata` too.
DEV_VOLUME = re.compile(r"(?:[a-z0-9_.-]+_)?transit_(?:pgdata|chdata)")
# `-v`/`--volumes` on `docker rm`, alone or folded into a short-flag cluster (`-fv`).
VOLUME_FLAG = re.compile(r"--volumes(=\S*)?|-[a-z]*v[a-z]*")
LONG_VOLUME_FLAG = re.compile(r"--volumes(=\S*)?")
# What separates words for the teardown checks: whitespace, quotes and shell
# punctuation. Shell grammar is deliberately ignored, so a teardown inside
# quotes, after `bash -c`, or glued to `;` or `>` is read like any other.
_WORD_SPLIT = re.compile(r"[\s;&|<>()`'\"]+")
# Docker CLI global options that take their value as the next token, so the
# subcommand is read after the value rather than mistaken for it.
DOCKER_VALUE_FLAGS = {
    "-c",
    "--context",
    "-h",
    "--host",
    "--config",
    "-l",
    "--log-level",
    "--tlscacert",
    "--tlscert",
    "--tlskey",
}
# The shell's DATABASE_URL is the dev database, so a command that expands it is
# treated as aimed there. An inline `DATABASE_URL=<throwaway> cmd
# "$DATABASE_URL"` assignment does not reach the expansion, which the shell
# performs first, and a preceding `export` is not told apart either; naming the
# throwaway URL itself is the way to write to it.
DATABASE_URL_REF = re.compile(r"\$\{?DATABASE_URL\b")


def normalise_docker(tokens: list[str]) -> list[str]:
    """Spell the standalone `docker-compose` binary as `docker compose`, and a
    path-invoked binary by its bare name, so one set of checks covers every
    spelling of the same command. A URL whose path ends in `docker` is an
    argument, not the binary."""
    out: list[str] = []
    for tok in tokens:
        name = tok.rsplit("/", 1)[-1]
        if "://" in tok:
            out.append(tok)
        elif name == "docker-compose":
            out += ["docker", "compose"]
        elif name == "docker":
            out.append(name)
        else:
            out.append(tok)
    return out


def docker_subcommands(lowered: list[str]) -> list[list[str]]:
    """The first two words after each `docker`, past its global options."""
    found = []
    for i, tok in enumerate(lowered):
        if tok != "docker":
            continue
        j = i + 1
        while j < len(lowered) and lowered[j].startswith("-"):
            j += 2 if lowered[j] in DOCKER_VALUE_FLAGS else 1
        found.append(lowered[j : j + 2])
    return found


def destroys_dev_volume(cmd: str) -> bool:
    """A teardown that removes the dataset's volume carries no SQL keyword, so
    it is a block on its own rather than a target waiting for a mutation.

    Read as a bag of words over the whole command, every check evaluated: a
    check that stopped at one statement's trigger would let a later statement's
    teardown through. An unrelated `-v` elsewhere in the command, or a quoted
    mention of a teardown, can block instead, the cheap direction.
    """
    words = normalise_docker([w for w in _WORD_SPLIT.split(cmd.lower()) if w])
    if "docker" not in words:
        return False
    present = set(words)
    # Targets resolved at run time can name the dataset without spelling it.
    computed = "xargs" in present or "$" in cmd or "`" in cmd
    compose_down_v = "compose" in present and "down" in present and any(VOLUME_FLAG.fullmatch(w) for w in words)
    volume_rm = (
        "volume" in present
        and bool({"rm", "remove"} & present)
        and (computed or any(DEV_VOLUME.fullmatch(w) for w in words))
    )
    prune = "prune" in present and ("volume" in present or any(LONG_VOLUME_FLAG.fullmatch(w) for w in words))
    # Docker's own `rm`, not an `rm` run inside a container by `docker exec`.
    rm_v = (
        any(
            sub[:1] == ["rm"] or sub in (["container", "rm"], ["container", "remove"])
            for sub in docker_subcommands(words)
        )
        and (computed or any(w in DEV_CONTAINERS or CONTAINER.fullmatch(w) for w in words))
        and any(VOLUME_FLAG.fullmatch(w) for w in words)
    )
    return compose_down_v or volume_rm or prune or rm_v


def runs_a_sql_script(lowered: list[str]) -> bool:
    """`psql -f file.sql` carries its statements in a file this hook can't read.

    Structural rather than textual: the flag is what makes the command a write,
    and nothing in the visible text says so.
    """
    return "psql" in lowered and ("-f" in lowered or "--file" in lowered)


def runs_destructive_command(tokens: list[str]) -> bool:
    """A destructive Make target or `gtfs_pipeline` subcommand, in any simple
    command, that writes a store its own assignments do not point at the
    throwaway stack.

    A token holding whitespace is a quoted script (`bash -c "..."`, `ssh host
    "..."`) and is read as a command line of its own. Quoted prose naming a
    destructive target is read the same way and blocked, the cheap direction.
    """
    lowered = [t.lower() for t in tokens]
    for segment in _simple_commands(lowered):
        if _segment_writes_dev(segment):
            return True
        for tok in segment:
            if any(c.isspace() for c in tok):
                try:
                    inner = shlex.split(tok)
                except ValueError:
                    inner = tok.split()
                if runs_destructive_command(inner):
                    return True
    return False


_SHELL_SEPARATORS = {"&&", "||", ";", "|", "&"}
_ASSIGNMENT = re.compile(r"^[a-z_][a-z0-9_]*=")
_MAKE_NAMES = {"make", "gmake"}
# `python gtfs_pipeline.py ...` and `python -m gtfs_pipeline ...`.
_CLI_NAMES = {"gtfs_pipeline.py", "gtfs_pipeline"}


def _simple_commands(lowered: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for tok in lowered:
        if tok in _SHELL_SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(tok)
    return [segment for segment in segments if segment]


def _segment_writes_dev(segment: list[str]) -> bool:
    writes: set[str] = set()
    for i, tok in enumerate(segment):
        name = tok.rsplit("/", 1)[-1]
        if name in _MAKE_NAMES:
            for target in make_targets(segment[i + 1 :]):
                writes |= DESTRUCTIVE_TARGETS.get(target, set())
        elif name in _CLI_NAMES and i + 1 < len(segment):
            writes |= DESTRUCTIVE_SUBCOMMANDS.get(segment[i + 1], set())
    assignments = [tok for tok in segment if _ASSIGNMENT.match(tok)]
    return any(not any(THROWAWAY[store].match(a) for a in assignments) for store in writes)


# `make` options whose value is the next argument, not a target.
_MAKE_VALUE_FLAGS = {"-c", "-f", "-o", "--directory", "--file", "--makefile"}


def make_targets(args: list[str]) -> list[str]:
    """The targets in `make`'s own arguments: up to the next shell separator,
    past its flags and VAR=value assignments."""
    targets: list[str] = []
    skip = False
    for arg in args:
        if arg in _SHELL_SEPARATORS:
            break
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in _MAKE_VALUE_FLAGS
        elif "=" not in arg:
            targets.append(arg)
    return targets


def targets_dev_db(tokens: list[str], cmd: str) -> bool:
    lowered = [t.lower() for t in tokens]

    if DATABASE_URL_REF.search(cmd):
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
    tokens = normalise_docker(tokens)
    lowered = [t.lower() for t in tokens]
    if destroys_dev_volume(cmd) or runs_destructive_command(tokens):
        return True
    if not targets_dev_db(tokens, cmd):
        return False
    return bool(WRITE.search(cmd)) or runs_a_sql_script(lowered)


def read_command() -> str | None:
    """The Bash command in the hook payload, or None when the payload is unreadable."""
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "") or ""
    except (ValueError, AttributeError):
        return None
    return cmd if isinstance(cmd, str) else None


def main() -> int:
    cmd = read_command()
    if cmd is None:
        sys.stderr.write(
            "BLOCKED: guard_dev_db could not read the hook payload; refusing rather than waving the command through.\n"
        )
        return 2
    if should_block(cmd):
        sys.stderr.write(
            "BLOCKED: write or volume teardown against a dev store (Postgres :5433/:5543 / ClickHouse :8123 / "
            "the transit_pgdata and transit_chdata volumes) — both hold real production data and are read-only. "
            "Use the throwaway :5544 / :8124 pair; a volume teardown is judged over the whole command, "
            "so run it as its own call. See AGENTS.md.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
