"""PreToolUse(Bash) hook body: block writes aimed at either dev store.

Covers dev Postgres and dev ClickHouse. Both hold real production data and are
read-only for agents; the throwaway pair on :5544/:8124 is where writes belong.

DEV_PORTS must list every port a dev store is published on, not only the one
`compose.yml` declares: the container holding the dev dataset can be published
elsewhere. A port listed here that holds someone else's database is harmless,
since refusing to write to it is right either way. A port left out is the
dataset.

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

A quoted script (`sh -c "..."`) is judged as a command line of its own, and a
`clickhouse-client` that names no host, port or connection is treated as aimed at
the default, dev, instance.

Known gaps, still allowed:
- Statements whose text the hook cannot see: a variable (`-c "$SQL"`), a
  substitution that runs a program of its own (`-c "$(./gen_sql.sh)"`), a file
  fed through a reader outside the recognised ones, or a driver script
  (`python -c`, `python x.py`) that reads the statements itself.
- A dev store reached without naming it: a psql service file, `PGSERVICE` or
  `.pgpass` default; a ClickHouse `--connection` or config file; a host alias
  or tunnelled port other than DEV_PORTS and DEV_CONTAINERS.
- curl options read from a config file (`-K`).
- A `$(cat ...)` read anywhere in a command that also reads a dev store blocks it,
  since the substitution check is not scoped to the client's own simple command.
- A literal `|`, `&` or `;` argument on its own (`psql -F '|'`) reads as a shell
  operator, so it can block a read or split a command the hook then misjudges.
- A bare `make` run through a wrapper outside the recognised prefixes
  (`poetry run make`) or after a leading redirect (`</dev/null make`) is not
  read as running its default goal; a named destructive target still blocks.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

DEV_PORTS = ("5433", "5543", "8123", "9000")
DEV_SERVICES = {"db", "clickhouse"}
# Container names compose does not derive, which a dev store can still run under.
DEV_CONTAINERS = {"transit-pg", "transit-ch", "transit-pg-latest-main"}
# Throwaway stacks, per store. Naming one exempts a destructive command, which
# names no host of its own, from that store; it exempts nothing else.
# Per store, the variable that points it and the throwaway value of it. Read
# only from the command's own assignments -- `make_args` for a make target, the
# env prefix for a gtfs_pipeline call -- so a throwaway port named anywhere else
# (another command, a comment) points nothing at it.
THROWAWAY = {
    # The port of the URL's host, not a ":5544" anywhere in it (a password).
    "pg": ("DATABASE_URL", re.compile(r"[a-z0-9+.-]+://(?:[^@/]*@)?[^/@:]+:5544(?:/|$)")),
    "ch": ("CLICKHOUSE_PORT", re.compile(r"8124$")),
}
# Container names the throwaway stack runs under (see the transit-app-gotchas skill).
THROWAWAY_CONTAINER = re.compile(r"transit-test-[a-z0-9_.-]+")
# The ML replica's compose file and the containers it names (`name: transit-ml`
# in that file). Its clickhouse-client is not the dev one even with no port.
REPLICA_COMPOSE_FILE = "deploy/vps/compose.yml"
REPLICA_CONTAINER = re.compile(r"transit-ml-[a-z0-9_.-]+")
# Commands that write through DATABASE_URL and CLICKHOUSE_* when the caller
# overrides nothing -- for make the values .env sets, which outrank an env
# prefix (only make's own VAR=value arguments beat them), the shell's for the
# CLI -- which are the real dev stores. They name no host, port or
# container, so nothing else here can recognise what they are aimed at, and
# running one is the write: no SQL keyword shows on the command line. Each maps
# to the stores it writes; it runs only when the command points every one of
# them at the throwaway stack.
DESTRUCTIVE_TARGETS = {
    # `all: bootstrap` is also the default goal, which a make naming no target runs.
    "all": {"pg", "ch"},
    "analyze": {"pg", "ch"},
    "bootstrap": {"pg", "ch"},
    "analyze-all": {"pg", "ch"},
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
    "analyze": {"pg", "ch"},
    "analyze_all": {"pg", "ch"},
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
# `COPY t FROM` and `\copy ... FROM` load data in; `TO` reads it out and stays
# allowed. ClickHouse takes `RENAME a TO b` with no `TABLE` keyword, so any `RENAME`
# followed by an operand is a write. A bare `OPTIMIZE` only merges parts, so just `DEDUPLICATE` is listed;
# `SELECT ... INTO <table>` creates a table, `INTO OUTFILE` reads out.
#
# Those three -- `\copy ... from`, `OPTIMIZE ... DEDUPLICATE`, `SELECT ... INTO` --
# span several words, so they live in WRITE_STATEMENT and are matched per simple
# command rather than over the raw text: `|`, `&` and `&&` inside a quoted SQL
# argument are operators of the SQL (`a||b`, array overlap), while the same
# characters outside quotes end the shell command and must not join two commands
# into one statement. Only `;` ends the SQL statement.
WRITE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|REINDEX)\b"
    r"|\b(dropdb|createdb|pg_restore)\b"
    r"|\bVACUUM\s+FULL\b"
    r"|\bcopy\s+(?!\()[\w.\"\\]+(\s*\([^)]*\))?\s+from\b"
    r"|\brename\s+\S"
    r"|\b(detach|attach)\s+(table|database|dictionary|view|partition)\b"
    r"|\bexchange\s+(tables|dictionaries)\b"
    r"|\brefresh\s+materialized\s+view\b"
    r"|migrate[\s_-]*down|downgrade",
    re.IGNORECASE,
)
WRITE_STATEMENT = re.compile(
    r"\\copy\s+(?!\()[^;]*\bfrom\b"
    r"|\boptimize\b[^;]*\bdeduplicate\b"
    r"|\bselect\b[^;]*\binto\s+(?!outfile\b|dumpfile\b)",
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
# A dev port written as a `port=` key inside any token: a libpq keyword DSN
# (`host=h port=5433`) or URI query (`?port=5433`), a client kwarg
# (`connect(port=5433)`), or a `CLICKHOUSE_PORT=`/`PGPORT=` assignment, also
# one that ends in `;` or sits inside a `bash -c` string. DEV_PORTS is the one list of ports.
PORT_KEY = re.compile(r"(?<![a-z0-9])(?:pg)?port\s*=\s*['\"]?(?:" + "|".join(DEV_PORTS) + r")\b")

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


_SQL_CLIENTS = {"psql", "clickhouse-client", "clickhouse"}
_SCRIPT_FLAGS = {"--file", "--queries-file"}
# psql's `\i`/`\ir`/`\include` include, which sits inside the quoted `-c` argument.
_INCLUDE_META = re.compile(r"\\(?:ir?|include(?:_relative)?)\s")
# A curl short-option cluster that ends in `-T` (upload file) or carries `-d@file`.
_CURL_FILE_CLUSTER = re.compile(r"-[a-z]*(?:t|d@.*)")
# psql's short options that take a value; the rest of the word after one is that value.
_PSQL_VALUE_OPTIONS = frozenset("cdFhLopPRTUv")
_PSQL_LONG_FILE = re.compile(r"--fil(?:e)?(?:=.*)?")
# A curl form field whose content is a file (`name=@f`, `name=<f`), also glued to
# `-F` inside a cluster of value-less flags (`-sFname=@f`).
_FORM_FILE_VALUE = re.compile(r"[^=]*=[@<]")
_FORM_CLUSTER = re.compile(r"-[sSfLkvi]*F(.+)")
_FORM_CLUSTER_END = re.compile(r"-[sSfLkvi]*F")
# `--data-urlencode` reads a file for `@file` and `name@file`; `name=content` is literal.
_URLENCODE_FILE_VALUE = re.compile(r"[^=@]*@")
# A script read into an argument by command substitution: `"$(cat f)"`, `"$(< f)"`
# or the backtick spelling. Other substitutions (`$(date)`) read no script.
_SUBSTITUTION_READ = re.compile(
    r"(?:\$\(|`)\s*(?:(?:command\s+|exec\s+)?<|(?:command\s+|exec\s+)?(?:\S*/)?(?:cat|head|tail|tac|z?cat|gzcat|bzcat|xzcat|sed|awk|curl|wget)\b)"
)


def _command_names(tokens: list[str]) -> set[str]:
    """Basenames of the words in `tokens`, reading into a quoted script too, so a
    client inside `sh -c '...'` counts for the redirect or pipe around it."""
    names = {tok.rsplit("/", 1)[-1] for tok in tokens}
    for tok in tokens:
        if any(c.isspace() for c in tok):
            names |= {word.rsplit("/", 1)[-1] for word in _WORD_SPLIT.split(tok) if word}
    return names


def _feeds_a_client_stdin(shell: list[str]) -> bool:
    """An unquoted input redirect or here-doc (bar `< /dev/null`) in the same
    simple command as a SQL client, or a pipe into a client.

    Read from operator tokens, not the raw text: a `<` inside a quoted SQL
    argument (`<>`, `<=`, pgvector's `<->`) is a comparison, not a redirect, a
    glued `cat x.sql|psql` is still a pipe, and a redirect that belongs to
    another command (`psql ... > out && wc -l < out`) does not feed the client."""
    piped = False
    segment: list[str] = []

    def feeds(seg: list[str], was_piped: bool) -> bool:
        if not _SQL_CLIENTS & _command_names(seg):
            return False
        if was_piped:
            return True
        for i, tok in enumerate(seg):
            if "<" in tok and _is_separator(tok) and not tok.startswith("<("):
                if not (tok == "<" and seg[i + 1 : i + 2] == ["/dev/null"]):
                    return True
        return False

    for tok in [*shell, ";"]:
        if _is_separator(tok) and "<" not in tok and ">" not in tok:
            if segment:
                if feeds(segment, piped):
                    return True
                segment, piped = [], False
            piped = piped or ("|" in tok and tok != "||")
        else:
            segment.append(tok)
    return False


def _client_args(segment: list[str]) -> tuple[str, list[str]] | None:
    """The SQL client a simple command runs and the words after it, or None."""
    for i, tok in enumerate(segment):
        name = tok.rsplit("/", 1)[-1].lower()
        if name in _SQL_CLIENTS:
            return name, segment[i + 1 :]
    return None


def _psql_cluster_names_a_file(tok: str) -> bool:
    """A psql short-option cluster that carries `-f`, alone (`-fx.sql`) or after
    value-less flags (`-Atfx.sql`). The first option that takes a value ends the
    scan: the rest of the word is that value. Case matters here (`-F` is the
    field separator), so this reads the tokens as typed."""
    if not tok.startswith("-") or tok.startswith("--"):
        return False
    for ch in tok[1:]:
        if ch == "f":
            return True
        if ch in _PSQL_VALUE_OPTIONS:
            return False
    return False


def _reads_a_script_file(segments: list[list[str]]) -> bool:
    """`-f`/`--file`/`--queries-file` on a SQL client's own command line."""
    for segment in segments:
        found = _client_args(segment)
        if found is None:
            continue
        name, args = found
        for tok in args:
            low = tok.lower()
            if low in _SCRIPT_FLAGS or low.startswith(("--file=", "--queries-file=")):
                return True
            # `-f` is psql's file but ClickHouse's `--format`. getopt also
            # accepts an unambiguous prefix of a long option (`--fil`).
            if name == "psql" and (tok == "-f" or _PSQL_LONG_FILE.fullmatch(low) or _psql_cluster_names_a_file(tok)):
                return True
    return False


def _uploads_a_file_over_http(tokens: list[str]) -> bool:
    """A curl or wget body read from a file. Read as typed: curl's `-F` and
    `-f` are different options."""
    for i, tok in enumerate(tokens):
        following = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in ("--post-file", "--body-file") or tok.startswith(("--post-file=", "--body-file=")):
            return True
        if tok.startswith("@") or tok == "--upload-file" or _CURL_FILE_CLUSTER.fullmatch(tok.lower()):
            return True
        if tok == "--form" or _FORM_CLUSTER_END.fullmatch(tok):
            if _FORM_FILE_VALUE.match(following):
                return True
        elif (glued := _FORM_CLUSTER.fullmatch(tok)) and _FORM_FILE_VALUE.match(glued[1]):
            return True
        elif tok == "--data-urlencode" and _URLENCODE_FILE_VALUE.match(following):
            return True
    return False


def runs_a_sql_script(cmd: str) -> bool:
    """A script run whose statements this hook cannot read: `psql -f file.sql`,
    a client fed on stdin or by pipe, an `\\i` include, a file read by command
    substitution, or an HTTP body read from a file.

    Structural rather than textual: the way the statements arrive is what makes
    the command a write, and nothing in the visible text says so. A token holding
    whitespace is a quoted script (`sh -c "psql ... < f.sql"`) and is read as a
    command line of its own.
    """
    raw = _shell_tokens(cmd)
    shell = [tok.lower() for tok in raw]
    names = _command_names(shell)
    if names & _SQL_CLIENTS:
        if _reads_a_script_file(_simple_commands(raw)):
            return True
        if _feeds_a_client_stdin(shell) or _INCLUDE_META.search(cmd.lower()):
            return True
    if names & (_SQL_CLIENTS | {"curl", "wget"}) and _SUBSTITUTION_READ.search(cmd.lower()):
        return True
    if names & {"curl", "wget"} and _uploads_a_file_over_http(raw):
        return True
    return any(runs_a_sql_script(tok) for tok in raw if tok != cmd and any(c.isspace() for c in tok))


def writes_sql(cmd: str) -> bool:
    """A mutating statement anywhere in the command; see WRITE and WRITE_STATEMENT."""
    if WRITE.search(cmd):
        return True
    return any(WRITE_STATEMENT.search(" ".join(segment)) for segment in _simple_commands(_shell_tokens(cmd)))


def _is_dev_clickhouse_host(host: str) -> bool:
    """A host that still reaches the dev instance when no port is named: the
    loopback, or a dev container."""
    loopback = host in {"localhost", "::1", "0.0.0.0"} or host.startswith("127.")
    return loopback or host in DEV_CONTAINERS or bool(CONTAINER.fullmatch(host))


def _clickhouse_client_aims_elsewhere(segment: list[str], at: int) -> bool:
    """Whether a `clickhouse-client` at `segment[at]` names a port, a non-dev
    host, a connection, or runs in a throwaway container. A host alone does not:
    the port then defaults to the dev one."""
    rest = segment[at + 1 :]
    hosts: list[str] = []
    for i, tok in enumerate(rest):
        low = tok.lower()
        if low.startswith(("--port", "--connection")):
            return True
        if low in ("--host", "-h") and i + 1 < len(rest):
            hosts.append(rest[i + 1].lower())
        elif low.startswith("--host="):
            hosts.append(low[len("--host=") :])
        elif low.startswith("-h") and not low.startswith("--"):
            hosts.append(low[2:])
    for tok in segment:
        low = tok.lower()
        if low.startswith("clickhouse_port="):
            return True
        if low.startswith("clickhouse_host="):
            hosts.append(low.split("=", 1)[1])
    if any(not _is_dev_clickhouse_host(host) for host in hosts):
        return True
    before = [tok.lower() for tok in segment[:at]]
    # A throwaway container counts only as the target of the `docker exec` in
    # front of the client, not as a word elsewhere (a trailing comment).
    if any(THROWAWAY_CONTAINER.fullmatch(tok) for tok in before):
        return True
    # The ML replica is a store of its own: reached through its compose file or
    # by its `transit-ml-*` container name. Any other unnamed container could be
    # the dev instance under another name, so it is not exempted.
    return any(tok.endswith(REPLICA_COMPOSE_FILE) or REPLICA_CONTAINER.fullmatch(tok) for tok in before)


def runs_default_clickhouse_client(cmd: str) -> bool:
    """A `clickhouse-client` that names no port, connection or non-dev host, in
    any simple command: it connects to the default instance, which is the dev
    one."""
    for segment in _simple_commands(_shell_tokens(cmd)):
        for i, tok in enumerate(segment):
            name = tok.rsplit("/", 1)[-1].lower()
            if name == "clickhouse-client" or (name == "clickhouse" and segment[i + 1 : i + 2] == ["client"]):
                if not _clickhouse_client_aims_elsewhere(segment, i):
                    return True
            elif tok != cmd and any(c.isspace() for c in tok) and runs_default_clickhouse_client(tok):
                return True
    return False


# Variables that steer make itself. Set anywhere in a command -- an env prefix,
# an earlier `export`, around a quoted script -- they reach every make in it and
# can rewrite any variable, so no make target there counts as pointed.
_MAKE_CONTROL = re.compile(r"\b(?:GNUMAKEFLAGS|MAKEFLAGS|MFLAGS|MAKEOVERRIDES|MAKEFILES)\b")


def runs_destructive_command(cmd: str, make_controlled: bool = False, multiline: bool = False) -> bool:
    """A destructive Make target or `gtfs_pipeline` subcommand, in any simple
    command, that writes a store its own assignments do not point at the
    throwaway stack.

    A token holding whitespace is a quoted script (`bash -c "..."`, `ssh host
    "..."`) and is read as a command line of its own, as is the word after a
    shell's `-c` (`bash -c make`). Quoted prose naming a destructive target is read the
    same way and blocked, the cheap direction.
    """
    make_controlled = make_controlled or bool(_MAKE_CONTROL.search(cmd))
    # shlex reads a newline as plain whitespace, while bash ends a command there
    # (or not: a continuation, an escaped backslash, a comment holding a quote).
    # A destructive command spanning lines is blocked rather than re-derived.
    multiline = multiline or "\n" in cmd
    # Case is kept: make and the shell both match variable names case-sensitively.
    for segment in _simple_commands(_shell_tokens(cmd)):
        if _segment_writes_dev(segment, make_controlled, multiline):
            return True
        quoted = (
            tok
            for j, tok in enumerate(segment)
            if any(c.isspace() for c in tok) or (j > 1 and segment[j - 1] == "-c" and _runs_shell(segment[: j - 1]))
        )
        if any(runs_destructive_command(tok, make_controlled, multiline) for tok in quoted):
            return True
    return False


def _shell_tokens(text: str) -> list[str]:
    """shlex tokens with each run of shell operator characters (`;`, `&&`,
    `|`, `>` ...) a token of its own, also where it is glued to a word:
    `shlex.split("make a;ls")` keeps `a;ls` as one token."""
    # A `#` starts a comment only at the start of a word, which shlex's default
    # (any `#`) gets wrong: `pa#ss@host ... -f x.sql` would lose its flag. The
    # comment-aware lexer is the fallback for a trailing comment holding an
    # unbalanced quote, which the comment-free one cannot lex.
    for commenters in ("", "#"):
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = commenters
        try:
            return list(lexer)
        except ValueError:
            continue
    return text.split()


_OPERATOR_CHARS = frozenset("();<>|&")


def _is_separator(tok: str) -> bool:
    return bool(tok) and set(tok) <= _OPERATOR_CHARS


_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_MAKE_NAMES = {"make", "gmake"}
# `python gtfs_pipeline.py ...` and `python -m gtfs_pipeline ...`.
_CLI_NAMES = {"gtfs_pipeline.py", "gtfs_pipeline"}


def _simple_commands(lowered: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for tok in lowered:
        if _is_separator(tok):
            segments.append([])
        else:
            segments[-1].append(tok)
    return [segment for segment in segments if segment]


def _segment_writes_dev(segment: list[str], make_controlled: bool, multiline: bool) -> bool:
    for i, tok in enumerate(segment):
        name = tok.rsplit("/", 1)[-1].lower()
        if name in _MAKE_NAMES:
            # The Makefile's `-include .env` outranks the environment, so an env
            # prefix does not repoint a make target; only make's own VAR=value
            # arguments do.
            args = segment[i + 1 :]
            targets, assignments, trusted = make_args(args)
            # xargs appends words from its input after these, which can name a
            # target or a later, winning assignment the guard never sees.
            if any(tok.rsplit("/", 1)[-1].lower() == "xargs" for tok in segment[:i]):
                trusted = False
            run = _in_command_position(segment, i)
            if run and _MAKE_INFO_FLAGS & set(args):
                continue
            writes: set[str] = set()
            # Only a make that runs has a default goal (`all`, also an English
            # word in prose), or can run one a flag's value or an expansion hid.
            for target in targets or (["all"] if run else []):
                if run or target.lower() != "all":
                    writes |= DESTRUCTIVE_TARGETS.get(target.lower(), set())
            if run and not trusted:
                writes |= DESTRUCTIVE_TARGETS["all"]
            if writes and (make_controlled or (multiline and run) or not trusted):
                return True
            if _points_at_dev(writes, assignments):
                return True
        elif name in _CLI_NAMES and i + 1 < len(segment):
            # Nothing loads `.env` for a direct CLI call, so its env prefix is its target.
            cli_writes = DESTRUCTIVE_SUBCOMMANDS.get(segment[i + 1].lower(), set())
            if cli_writes and multiline:
                return True
            if _points_at_dev(cli_writes, segment[:i]):
                return True
    return False


# Shell words and programs that run the command after them.
_COMMAND_PREFIXES = frozenset(
    {
        "env",
        "do",
        "then",
        "else",
        "elif",
        "if",
        "while",
        "until",
        "!",
        "{",
        "time",
        "exec",
        "eval",
        "nohup",
        "nice",
        "xargs",
        "timeout",
        "sudo",
        "caffeinate",
        "stdbuf",
        "command",
    }
)
# A prefix's flags whose value is the next word (`sudo -u user`, `nice -n 5`,
# `xargs -n 1`, `caffeinate -t 60` ...).
_PREFIX_VALUE_FLAGS = frozenset({"-u", "-g", "-n", "-L", "-P", "-I", "-d", "-t", "-s", "-k", "-C", "-p"})
# Prefixes whose -v/-V prints instead of running the command. `env -v` and an
# external `time -v` still run it; the shell's `time` keyword cannot be told
# from that here, so it counts as running too.
_PRINTS_ON_V = frozenset({"command", "sudo"})
# timeout's duration, the one bare value a prefix takes before its command.
_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?")
# Shells whose -c takes the script to run.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# Flags with which make only prints information and runs no target.
_MAKE_INFO_FLAGS = frozenset({"-v", "--version", "-h", "--help"})


def _in_command_position(segment: list[str], i: int) -> bool:
    """Whether segment[i] is the program the simple command runs: every word
    before it is an assignment, a run-through prefix (`env`, `sudo`, a loop's
    `do` ...), or such a prefix's own flag, flag value or duration. Any other
    word is the program run instead (`sudo apt install make`), and `command -v
    make`, `which make` and prose naming make do not run it."""
    after_prefix = False
    expect_value = False
    prefix = ""
    for tok in segment[:i]:
        name = tok.rsplit("/", 1)[-1].lower()
        if expect_value:
            expect_value = False
        elif _ASSIGNMENT.match(tok):
            pass
        elif name in _COMMAND_PREFIXES:
            after_prefix = True
            prefix = name
        elif prefix in _PRINTS_ON_V and tok in ("-v", "-V"):
            return False
        elif after_prefix and tok.startswith("-"):
            expect_value = tok in _PREFIX_VALUE_FLAGS
        elif after_prefix and _DURATION.fullmatch(tok):
            pass
        else:
            return False
    return True


def _runs_shell(words: list[str]) -> bool:
    """Whether the words before a `-c` end in a shell (`bash`, `sudo sh` ...)."""
    return bool(words) and words[-1].rsplit("/", 1)[-1].lower() in _SHELLS


def _points_at_dev(writes: set[str], words: list[str]) -> bool:
    """Whether any store in *writes* is left pointed at dev by the NAME=value
    assignments among *words*. Later assignments win."""
    assigned = dict(tok.split("=", 1) for tok in words if _ASSIGNMENT.match(tok))
    for store in writes:
        name, throwaway = THROWAWAY[store]
        value = assigned.get(name, "").lower()
        # libpq lets a URL query (`?port=05433`, `?host=`) override the host:port,
        # and parses it loosely, so a throwaway URL carries none.
        if not _SAFE_VALUE.fullmatch(value) or not throwaway.match(value) or "?" in value:
            return True
    return False


# The only make flags a destructive target may carry. Make can read a variable
# from almost any other word -- an unlisted flag's value or the word after a flag
# that takes none, `$` expansion, a non-plain `=` form, `--eval` text -- so any
# such word makes the whole command untrusted (a false block is the cheap
# direction). `-C` takes the directory after it.
_SAFE_MAKE_FLAG = re.compile(r"-[sk]+|-j[0-9]*|--silent|--keep-going|--no-print-directory|--jobs(?:=[0-9]+)?")
# `-j`/`--jobs` take an optional job count, which may be the next word.
_SHORT_JOBS_FLAG = re.compile(r"-[sk]*j")
# Make pastes a variable's value into recipe shell text, and the shell expands an
# env-prefix value, so only URL-safe characters are trusted in either.
_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.,:/@%+=?~-]*")
# The only variables a destructive make command may set: the ones its recipes
# read. Any other can repoint a recipe (`db_url`), its shell (`SHELL`) or a
# sub-make (`MAKE`).
_SAFE_MAKE_VARIABLES = frozenset(
    {
        "DATABASE_URL",
        "CLICKHOUSE_PORT",
        "CLICKHOUSE_DATABASE",
        "AGENCY_ID",
        "CONFIRM",
        "CSV",
        "DAY",
        "DAYS",
        "FOLDER",
        "FULL",
        "LOCALE",
        "STATIC_PATH",
        "TARGET",
    }
)


def make_args(args: list[str]) -> tuple[list[str], list[str], bool]:
    """The targets and the plain NAME=value assignments in `make`'s own
    arguments, up to the next shell separator, and whether every word was one
    of those (with an allowlisted name and a URL-safe value), an allowlisted
    flag, or `-C <dir>`.
    A `#` word starts a comment the guard cannot place, so it is untrusted."""
    targets: list[str] = []
    assignments: list[str] = []
    trusted = True
    after_directory_flag = False
    after_jobs_flag = False
    for arg in args:
        if _is_separator(arg):
            break
        if after_jobs_flag:
            after_jobs_flag = False
            if arg.isdigit():
                continue
        # The shell expands `$` and backticks into words the guard never sees.
        if arg.startswith("#") or "$" in arg or "`" in arg:
            trusted = False
        elif after_directory_flag:
            after_directory_flag = False
        elif arg == "-C":
            after_directory_flag = True
        elif arg == "--jobs" or _SHORT_JOBS_FLAG.fullmatch(arg):
            after_jobs_flag = True
        elif arg.startswith("-"):
            trusted = trusted and bool(_SAFE_MAKE_FLAG.fullmatch(arg))
        elif "=" in arg:
            name, _, value = arg.partition("=")
            if _ASSIGNMENT.match(arg) and name in _SAFE_MAKE_VARIABLES and _SAFE_VALUE.fullmatch(value):
                assignments.append(arg)
            else:
                trusted = False
        else:
            targets.append(arg)
    return targets, assignments, trusted


def targets_dev_db(tokens: list[str], cmd: str) -> bool:
    lowered = [t.lower() for t in tokens]

    if DATABASE_URL_REF.search(cmd):
        return True

    for i, tok in enumerate(lowered):
        if PORT_KEY.search(tok):
            return True
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


def _with_nested_tokens(tokens: list[str]) -> list[str]:
    """`tokens` plus the words of each quoted script in them, so a target named
    only inside `sh -c '...'` pairs with a statement outside it."""
    out = list(tokens)
    for tok in tokens:
        if any(c.isspace() for c in tok):
            out += _with_nested_tokens(_shell_tokens(tok))
    return out


def should_block(cmd: str) -> bool:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # Unbalanced quotes: fall back to whitespace splitting rather than give
        # up, so a malformed command can't slip past by failing to tokenize.
        tokens = cmd.split()
    tokens = normalise_docker(tokens)
    if destroys_dev_volume(cmd) or runs_destructive_command(cmd):
        return True
    if runs_default_clickhouse_client(cmd) and (writes_sql(cmd) or runs_a_sql_script(cmd)):
        return True
    if targets_dev_db(_with_nested_tokens(tokens), cmd) and (writes_sql(cmd) or runs_a_sql_script(cmd)):
        return True
    # A quoted script (`sh -c "psql -p ... < f.sql"`) can hold the target and the
    # statement together, so it is judged as a command line of its own.
    return any(should_block(tok) for tok in _shell_tokens(cmd) if tok != cmd and any(c.isspace() for c in tok))


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
            "BLOCKED: write or volume teardown against a dev store (Postgres :5433/:5543 / ClickHouse :8123/:9000 / "
            "the transit_pgdata and transit_chdata volumes) — both hold real production data and are read-only. "
            "Use the throwaway :5544 / :8124 pair; a volume teardown is judged over the whole command, "
            "so run it as its own call. A destructive make target runs only as "
            "`make <target> DATABASE_URL=...:5544/... CLICKHOUSE_PORT=8124` (an env prefix before `make` loses "
            "to .env; one line, flags limited to -s, -k, -j, -C <dir>, --no-print-directory, setting only "
            "variables its recipes read, with URL-safe values), and a gtfs_pipeline subcommand only with those "
            "assigned as its env prefix, for each store it writes. See AGENTS.md.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
