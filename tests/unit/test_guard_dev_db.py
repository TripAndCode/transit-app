"""Behavioural tests for the dev-store write guard (`.claude/hooks/guard-dev-db.sh`).

The hook is the last automated thing standing between an agent and a write
against the real dev Postgres (:5433) or dev ClickHouse (:8123), and it is
edited by hand whenever container names or ports move. Every case below is
driven through the shell entry point settings.json actually registers, so the
wrapper and the Python body are both covered rather than only the importable
half.

A blocked case either pairs a dev target with a mutating statement or tears
down a dev volume, which needs no SQL keyword; an allowed case keeps one half of
the pair away or tears down only throwaway state. An unreadable payload is
refused. The commands are only ever JSON string payloads fed to the hook's stdin
parser — nothing here executes them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "guard-dev-db.sh"

BLOCKED = [
    pytest.param('psql postgresql://transit:transit@localhost:5433/transit -c "DROP TABLE updates"', id="url"),
    pytest.param('psql -h localhost -p 5433 -U transit -c "DELETE FROM updates"', id="separated-host-port"),
    pytest.param('psql -h 127.0.0.1 -p 5433 -U transit -c "DELETE FROM updates"', id="loopback-ip"),
    pytest.param('PGPORT=5433 PGHOST=localhost psql -U transit -c "DELETE FROM updates"', id="env-port"),
    # The dev dataset is not always on the port compose.yml declares -- the
    # live container publishes :5543 -- and the guard has to cover the port
    # the data is actually reachable on, not the documented one.
    pytest.param(
        'psql postgresql://transit:transit@localhost:5543/transit -c "DROP TABLE updates"', id="alt-dev-port-url"
    ),
    pytest.param('psql -h localhost -p 5543 -U transit -c "DELETE FROM updates"', id="alt-dev-port-separated"),
    pytest.param('docker compose exec db psql -U transit -c "TRUNCATE updates"', id="compose-exec"),
    pytest.param('docker compose run --rm db psql -h db -U transit -c "DROP TABLE updates"', id="compose-run"),
    pytest.param(
        'docker compose --project-name transit exec db psql -U transit -c "DROP TABLE updates"',
        id="compose-global-flag",
    ),
    pytest.param('docker exec transit-app-db-1 psql -U transit -c "DROP TABLE updates"', id="derived-container"),
    pytest.param('docker exec transit-pg psql -U transit -c "DROP TABLE updates"', id="legacy-container"),
    pytest.param("make migrate-down CONFIRM=1", id="migrate-down-inherits-dev-default"),
    # The CLI's own spelling of a down migration, aimed at either dev port.
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5433/transit poetry run python gtfs_pipeline.py "
        "migrate down",
        id="cli-migrate-down-dev-port",
    ),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5543/transit python gtfs_pipeline.py migrate down "
        "--target 0001",
        id="cli-migrate-down-target-alt-dev-port",
    ),
    pytest.param("poetry run python gtfs_pipeline.py migrate down", id="cli-migrate-down-shell-database-url"),
    pytest.param("poetry run python ./gtfs_pipeline.py prune_query_log --days 1", id="cli-prune-shell-database-url"),
    # Make targets that write through .env's dev defaults, and name nothing.
    pytest.param("make prune-personal-data", id="make-prune-personal-data"),
    pytest.param("make prune-query-log", id="make-prune-query-log"),
    pytest.param("make -C . prune-admin-audit", id="make-dir-flag-prune-admin-audit"),
    pytest.param("make ch-bootstrap", id="make-ch-bootstrap"),
    pytest.param("CLICKHOUSE_PORT=8123 make ch-bootstrap", id="make-ch-bootstrap-dev-port"),
    pytest.param("make migrate", id="make-migrate"),
    pytest.param("make db", id="make-db"),
    pytest.param("make analyze-all", id="make-analyze-all"),
    pytest.param("make seed-agencies", id="make-seed-agencies"),
    pytest.param("make lint && make build-rag-index", id="make-second-command-build-rag-index"),
    # A quoted script, a renamed make, and the CLI run as a module.
    pytest.param('bash -c "make prune-query-log"', id="make-target-inside-bash-c"),
    pytest.param("gmake prune-query-log", id="gmake-prune-query-log"),
    pytest.param("poetry run python -m gtfs_pipeline migrate down", id="cli-as-module-migrate-down"),
    # A throwaway port named outside the command's own assignments exempts nothing.
    pytest.param("make prune-query-log # :5544", id="throwaway-port-only-in-a-comment"),
    pytest.param(
        "make prune-query-log && psql postgresql://transit:transit@localhost:5544/transit_test -c 'SELECT 1'",
        id="throwaway-port-only-in-another-command",
    ),
    # The last of two assignments is the one the command sees.
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test "
        "DATABASE_URL=postgresql://transit:transit@localhost:5433/transit make prune-query-log",
        id="later-assignment-points-back-at-dev",
    ),
    # A separator glued to a word still ends the simple command.
    pytest.param("make prune-query-log;ls", id="semicolon-glued-to-the-target"),
    pytest.param("echo hi|make prune-query-log", id="pipe-glued-before-make"),
    pytest.param("true||make prune-query-log", id="or-glued-before-make"),
    pytest.param('bash -c "make prune-query-log;ls"', id="glued-semicolon-inside-bash-c"),
    # :5544 in the password is not the port the URL connects to.
    pytest.param(
        "DATABASE_URL=postgresql://transit:5544@localhost:5433/transit make prune-query-log",
        id="throwaway-port-only-in-the-password",
    ),
    # bootstrap runs `$(MAKE) db` and `$(MAKE) seed-agencies`.
    pytest.param("make bootstrap", id="make-bootstrap"),
    # ingest writes both stores; pointing Postgres away leaves ClickHouse on dev.
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test make ingest FOLDER=raw",
        id="make-ingest-postgres-redirected-only",
    ),
    # Postgres CLIs that mutate without ever spelling a SQL keyword.
    pytest.param("dropdb -h transit-pg transit", id="dropdb"),
    pytest.param("createdb -h transit-pg transit_extra", id="createdb"),
    pytest.param("pg_restore -h transit-pg -d transit backup.dump", id="pg_restore"),
    # The statements live in a file this hook cannot read; the flag is the
    # only evidence, so it has to be enough.
    pytest.param("psql -h transit-pg -d transit -f migration.sql", id="psql-script-file"),
    pytest.param(
        r"psql postgresql://transit:transit@localhost:5433/transit -c \copy stops from '/tmp/stops.csv' csv",
        id="copy-from-loads-data-in",
    ),
    pytest.param('psql postgresql://transit:transit@localhost:5433/transit -c "REINDEX TABLE stops"', id="reindex"),
    pytest.param('psql postgresql://transit:transit@localhost:5433/transit -c "VACUUM FULL stops"', id="vacuum-full"),
    # Dev ClickHouse, reached by its pinned container name, its port, or the
    # compose service.
    pytest.param("clickhouse-client --host transit-ch --query 'INSERT INTO updates VALUES (1)'", id="ch-client-insert"),
    pytest.param("clickhouse-client --host transit-ch --query 'TRUNCATE TABLE updates'", id="ch-client-truncate"),
    pytest.param(
        "curl -s 'http://localhost:8123/' --data-binary 'ALTER TABLE updates DELETE WHERE 1=1'",
        id="ch-http-port",
    ),
    pytest.param("curl -s 'http://transit-ch:8123/' --data-binary 'DROP TABLE updates'", id="ch-http-container"),
    pytest.param("docker compose exec clickhouse clickhouse-client -q 'DROP TABLE updates'", id="ch-compose-exec"),
    # Naming a dev store next to a write keyword is enough on its own, even
    # when the command only searches text. Blocking is the cheap direction:
    # this costs a rephrase, the alternative costs the dataset.
    pytest.param("grep -R 'transit-ch' docs/ | grep INSERT", id="mention-without-intent-still-blocks"),
    # Volume teardown removes the dataset without a SQL keyword in sight.
    pytest.param("docker compose down -v", id="compose-down-volumes"),
    pytest.param("docker compose down --volumes --remove-orphans", id="compose-down-volumes-long"),
    pytest.param("docker volume rm transit-app_transit_pgdata", id="volume-rm-project-prefixed"),
    pytest.param("docker volume rm transit_chdata", id="volume-rm-bare"),
    pytest.param("docker volume prune -f", id="volume-prune"),
    pytest.param("docker system prune --volumes -f", id="system-prune-volumes"),
    pytest.param("docker rm -v transit-pg", id="rm-legacy-container-with-volume"),
    pytest.param("docker rm -fv transit-app-db-1", id="rm-derived-container-clustered-flags"),
    pytest.param("docker container rm -fv transit-pg", id="container-rm"),
    pytest.param("docker --context default rm -v transit-pg", id="rm-after-global-option"),
    # The standalone v1 binary and a path-invoked one are the same commands.
    pytest.param("docker-compose down -v", id="hyphenated-compose-down-volumes"),
    pytest.param('docker-compose exec db psql -U transit -c "DROP TABLE updates"', id="hyphenated-compose-exec"),
    pytest.param("/usr/local/bin/docker volume rm transit_pgdata", id="path-invoked-docker"),
    # Any expansion of the shell's DATABASE_URL counts as the dev database; name a
    # throwaway URL literally.
    pytest.param('psql "$DATABASE_URL" -c "DELETE FROM agencies WHERE agency_id = 9"', id="database-url-write"),
    pytest.param('psql "${DATABASE_URL}" -f fix.sql', id="database-url-braced-script"),
    # The shell expands "$DATABASE_URL" before the inline assignment applies, so
    # psql receives the shell's own value: the dev database.
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test "
        'psql "${DATABASE_URL}" -c "DROP TABLE x"',
        id="inline-assignment-does-not-reach-the-expansion",
    ),
    # A volume list computed at run time can name the dataset without spelling it.
    pytest.param("docker volume rm $(docker volume ls -q)", id="volume-rm-computed-list"),
    pytest.param("docker volume ls -q | xargs docker volume rm", id="volume-rm-via-xargs"),
    pytest.param("docker rm -v $(docker ps -aq --filter name=transit-pg)", id="rm-v-computed-list"),
    pytest.param("docker ps -q --filter name=transit-pg | xargs docker rm -v", id="rm-v-via-xargs"),
    # Every check runs over the whole command, so a harmless first statement
    # cannot end the evaluation before a later statement's teardown is read.
    pytest.param("docker compose down\ndocker volume rm transit_pgdata", id="teardown-after-a-newline"),
    pytest.param("docker compose down && docker volume rm transit-app_transit_pgdata", id="teardown-after-and"),
    pytest.param("docker volume ls; docker rm -v transit-pg", id="rm-v-after-a-volume-statement"),
    pytest.param("docker compose down \\\n  -v", id="down-v-across-a-line-continuation"),
    pytest.param(
        "docker compose down && docker volume rm transit_pgdata  # don't keep it", id="teardown-beside-an-apostrophe"
    ),
    # Parentheses glued to a word still leave the word itself readable.
    pytest.param("(cd /srv/app && docker compose down -v)", id="down-v-in-a-subshell"),
    pytest.param("(docker volume rm transit_pgdata)", id="volume-rm-in-a-subshell"),
    pytest.param("x=$(docker volume rm transit_pgdata)", id="volume-rm-in-a-substitution"),
    pytest.param("docker compose down --volumes=true", id="down-volumes-with-a-value"),
    pytest.param("docker --tlscacert ca.pem rm -v transit-pg", id="rm-after-a-tls-option"),
    # Shell operators glued to the last word, and teardowns inside quotes.
    pytest.param("docker compose down -v; docker compose up -d", id="down-v-before-a-semicolon"),
    pytest.param("docker volume rm transit_pgdata;", id="volume-rm-before-a-semicolon"),
    pytest.param("docker rm -fv transit-pg>/dev/null", id="rm-v-before-a-redirect"),
    pytest.param("docker compose down -v&&docker compose up -d", id="down-v-before-an-unspaced-and"),
    pytest.param('bash -c "docker compose down -v"', id="down-v-inside-bash-c"),
    pytest.param('x="$(docker volume rm transit_pgdata)"', id="volume-rm-in-a-quoted-substitution"),
    pytest.param(
        "# don't keep it\ndocker volume rm transit_pgdata\n# it's gone", id="teardown-between-two-apostrophes"
    ),
    pytest.param('docker volume rm "transit_pgdata"  # don\'t keep it', id="quoted-volume-beside-an-apostrophe"),
    # Flag spellings with a value or folded into a cluster.
    pytest.param("docker rm --volumes=true transit-pg", id="rm-volumes-with-a-value"),
    pytest.param("docker system prune --volumes=true -f", id="prune-volumes-with-a-value"),
    pytest.param("docker compose down -vt 5", id="down-v-in-a-flag-cluster"),
    # Docker Desktop's CLI location on macOS.
    pytest.param("$HOME/.docker/bin/docker volume rm transit_pgdata", id="docker-desktop-cli-path"),
    # A URL whose path ends in `docker` is not the docker binary.
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/docker -c "DROP TABLE x"', id="url-ending-in-docker"
    ),
    # The dev Postgres container as it runs today, created outside compose.
    pytest.param(
        'docker exec transit-pg-latest-main psql -U transit -c "DROP TABLE agencies"', id="current-dev-pg-container"
    ),
]

ALLOWED = [
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "SELECT count(*) FROM updates"', id="dev-read"
    ),
    pytest.param("docker compose exec db psql -U transit -c 'EXPLAIN SELECT 1'", id="dev-explain"),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5543/transit -c "SELECT count(*) FROM agencies"',
        id="alt-dev-port-read",
    ),
    pytest.param('psql postgresql://transit:transit@localhost:5544/transit_test -c "DROP TABLE updates"', id="test-db"),
    pytest.param('psql -h localhost -p 5544 -U transit -c "DELETE FROM updates"', id="test-db-separated"),
    pytest.param("ls -la", id="unrelated"),
    pytest.param('echo "DROP TABLE updates"', id="write-without-target"),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test make migrate-down CONFIRM=1",
        id="migrate-down-pointed-at-test-db",
    ),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test poetry run python gtfs_pipeline.py "
        "migrate down",
        id="cli-migrate-down-pointed-at-test-db",
    ),
    pytest.param("CLICKHOUSE_PORT=8124 make ch-bootstrap", id="ch-bootstrap-pointed-at-test-ch"),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test CLICKHOUSE_PORT=8124 "
        "make ingest FOLDER=raw",
        id="ingest-pointed-at-both-test-stores",
    ),
    pytest.param("make check-aggs", id="make-read-only-target"),
    pytest.param("poetry run python gtfs_pipeline.py check_aggs", id="cli-read-only-subcommand"),
    pytest.param("make test && make lint", id="make-throwaway-and-static-targets"),
    pytest.param('git commit -m "make it faster"', id="quoted-prose-naming-no-target"),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test make prune-query-log>out.log",
        id="test-db-with-a-glued-redirect",
    ),
    pytest.param(
        "make migrate-down CONFIRM=1 DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test",
        id="test-db-as-a-make-variable",
    ),
    pytest.param(
        r"psql postgresql://transit:transit@localhost:5433/transit -c \copy stops to '/tmp/stops.csv' csv",
        id="copy-to-reads-data-out",
    ),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "VACUUM stops"',
        id="plain-vacuum-locks-nothing",
    ),
    pytest.param("clickhouse-client --host transit-ch --query 'SELECT count() FROM updates'", id="ch-read"),
    pytest.param("curl -s 'http://localhost:8124/' --data-binary 'INSERT INTO updates VALUES (1)'", id="test-ch"),
    pytest.param("docker compose down", id="compose-down-keeps-volumes"),
    pytest.param("docker rm -f -v transit-test-pg transit-test-ch", id="test-containers-with-volume"),
    pytest.param("docker volume rm transit-test-pgdata", id="volume-rm-throwaway"),
    pytest.param("docker compose down && docker compose up -d", id="compose-restart-keeps-volumes"),
    pytest.param("docker exec transit-pg rm -rfv /tmp/x", id="rm-inside-a-container-is-not-docker-rm"),
    pytest.param("docker-compose down", id="hyphenated-compose-down-keeps-volumes"),
    pytest.param('psql "$DATABASE_URL" -c "SELECT count(*) FROM agencies"', id="database-url-read"),
    pytest.param(
        'psql "postgresql://transit:transit@localhost:5544/transit_test" -c "DROP TABLE x"',
        id="explicit-test-url-write",
    ),
]


def _run(command: str) -> int:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run([str(HOOK)], input=payload, text=True, capture_output=True).returncode


@pytest.mark.parametrize("command", BLOCKED)
def test_write_against_dev_db_is_blocked(command):
    assert _run(command) == 2, f"guard let a dev-DB write through: {command}"


@pytest.mark.parametrize("command", ALLOWED)
def test_safe_command_is_allowed(command):
    assert _run(command) == 0, f"guard blocked a safe command: {command}"


@pytest.mark.parametrize(
    "payload", ["not json", '{"tool_input": {"command": 5}}', '{"tool_input": "docker compose down -v"}']
)
def test_unreadable_input_is_refused(payload):
    """A payload the hook cannot parse is a hook that cannot see the command.
    Waving it through would make a broken harness the one way past the guard;
    refusing costs one visible failure."""
    assert subprocess.run([str(HOOK)], input=payload, text=True, capture_output=True).returncode == 2


def test_every_destructive_target_is_a_makefile_target():
    """The guard must not advertise a target it cannot gate."""
    assert set(_guard_module().DESTRUCTIVE_TARGETS) <= set(_make_recipes())


def _guard_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("guard_dev_db", HOOK.with_name("guard_dev_db.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_recipes() -> dict[str, str]:
    import re

    recipes: dict[str, list[str]] = {}
    current = None
    for line in (HOOK.parents[2] / "Makefile").read_text().splitlines():
        if match := re.match(r"^([A-Za-z0-9_.-]+):", line):
            current = match[1]
            recipes[current] = []
        elif current and line.startswith("\t"):
            recipes[current].append(line)
        elif line and not line.startswith("#"):
            current = None
    return {target: "\n".join(body) for target, body in recipes.items()}


# Targets and subcommands that reach a dev store without writing it, each
# checked by hand.
READ_ONLY_TARGETS = {
    "ask-eval",
    "check-aggs",
    "check-hash-token-cleanup",
    "check-migrations",
    "digest",
    "doctor",
    "serve",
}
READ_ONLY_SUBCOMMANDS = {"check_aggs", "check_migrations", "digest"}


def _targets_reaching_a_store() -> set[str]:
    """Targets whose recipe runs against $(db_url) or the ClickHouse client,
    directly or through a `$(MAKE) <target>` it calls."""
    import re

    recipes = _make_recipes()
    calls = {target: set(re.findall(r"\$\(MAKE\)\s+([A-Za-z0-9_.-]+)", recipe)) for target, recipe in recipes.items()}
    reaching = {
        target
        for target, recipe in recipes.items()
        if "$(db_url)" in recipe or "get_client" in recipe or "ch-bootstrap" in recipe
    }
    while True:
        grown = reaching | {target for target, called in calls.items() if called & reaching}
        if grown == reaching:
            return reaching
        reaching = grown


def test_every_make_target_reaching_a_store_is_classified():
    """A new target that runs against $(db_url) or the ClickHouse client, itself
    or through a target it calls, must be gated or deliberately named
    read-only; unlisted, the guard waves it through."""
    gated = set(_guard_module().DESTRUCTIVE_TARGETS)
    assert _targets_reaching_a_store() - gated - READ_ONLY_TARGETS == set()


def test_every_cli_subcommand_is_classified():
    """The same for `gtfs_pipeline.py`'s subcommands, which a bare CLI call runs
    against the shell's DATABASE_URL."""
    import re

    source = (HOOK.parents[2] / "gtfs_pipeline.py").read_text()
    subcommands = set(re.findall(r"sub\.add_parser\(\s*\"([^\"]+)\"", source))
    assert subcommands, "no subcommands found; the pattern no longer matches gtfs_pipeline.py"
    assert subcommands - set(_guard_module().DESTRUCTIVE_SUBCOMMANDS) - READ_ONLY_SUBCOMMANDS == set()
