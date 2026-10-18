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
    pytest.param("clickhouse-client --query 'INSERT INTO t VALUES (1)'", id="ch-client-bare-no-port"),
    pytest.param(
        "docker exec -it transit-ch clickhouse-client --query 'INSERT INTO t VALUES (1)'",
        id="ch-client-exec-dev-container",
    ),
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
    # An env prefix loses to `.env`, which the Makefile includes and exports: the
    # target still runs against the dev store `.env` names.
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test make migrate-down CONFIRM=1",
        id="migrate-down-test-db-only-as-env-prefix",
    ),
    pytest.param("CLICKHOUSE_PORT=8124 make ch-bootstrap", id="ch-bootstrap-test-ch-only-as-env-prefix"),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test CLICKHOUSE_PORT=8124 make analyze-all",
        id="analyze-all-test-stores-only-as-env-prefix",
    ),
    pytest.param("env CLICKHOUSE_PORT=8124 make ch-bootstrap", id="ch-bootstrap-test-ch-only-through-env"),
    # bootstrap runs `$(MAKE) db` and `$(MAKE) seed-agencies`.
    pytest.param("make bootstrap", id="make-bootstrap"),
    # ingest writes both stores; pointing Postgres away leaves ClickHouse on dev.
    pytest.param(
        "make ingest FOLDER=raw DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test",
        id="make-ingest-postgres-redirected-only",
    ),
    # analyze writes ClickHouse too (pipeline/ch_aggregates.py).
    pytest.param(
        "make analyze-all DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test",
        id="make-analyze-all-postgres-redirected-only",
    ),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test poetry run python gtfs_pipeline.py "
        "analyze_all",
        id="cli-analyze-all-postgres-redirected-only",
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
    # A script fed on stdin, by pipe, by `\i` or as an HTTP body is as unreadable
    # to the hook as `psql -f`.
    pytest.param("psql -h localhost -p 5433 -U transit transit < dump.sql", id="psql-stdin-redirect"),
    pytest.param("cat x.sql | psql postgresql://transit:transit@localhost:5433/transit", id="psql-piped-script"),
    pytest.param("docker compose exec -T db psql -U transit transit < x.sql", id="compose-exec-restore"),
    pytest.param(
        "docker compose exec -T clickhouse clickhouse-client < x.sql", id="compose-exec-clickhouse-stdin-redirect"
    ),
    pytest.param(
        "docker compose exec -T clickhouse clickhouse-client --queries-file x.sql", id="clickhouse-queries-file"
    ),
    pytest.param("cat x.sql|psql -h localhost -p 5433 -U transit transit", id="psql-glued-pipe"),
    pytest.param("cat x.sql | (psql -h localhost -p 5433 -U transit transit)", id="psql-pipe-into-subshell"),
    pytest.param("psql -h localhost -p 5433 -U transit -c '\\include fix.sql'", id="psql-include-long-alias"),
    pytest.param(
        "psql postgresql://transit:pa#ss@localhost:5433/transit -f fix.sql", id="hash-inside-password-then-file"
    ),
    pytest.param("curl -sT x.sql http://localhost:8123/", id="ch-http-upload-short-cluster"),
    pytest.param("psql -h localhost -p 5433 -U transit -c '\\i fix.sql'", id="psql-include-meta-command"),
    pytest.param("psql -h localhost -p 5433 -U transit <<'SQL'\nSELECT 1\nSQL", id="psql-heredoc"),
    pytest.param("curl -s http://localhost:8123/ --data-binary @x.sql", id="ch-http-body-from-file"),
    pytest.param("curl -s http://localhost:8123/ -T x.sql", id="ch-http-upload-file"),
    # Statements that mutate a store without any of the common write verbs.
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "COPY stops FROM STDIN csv" < stops.csv',
        id="sql-copy-from-stdin",
    ),
    pytest.param(
        "psql postgresql://transit:transit@localhost:5433/transit -c \"COPY stops (stop_id) FROM '/tmp/s.csv'\"",
        id="sql-copy-columns-from-file",
    ),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q 'RENAME TABLE updates TO updates_old'",
        id="ch-rename-table",
    ),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "COPY \\"Stops\\" FROM STDIN csv" < stops.csv',
        id="sql-copy-from-escaped-quoted-table",
    ),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q 'RENAME updates TO updates_old'",
        id="ch-rename-without-table-keyword",
    ),
    pytest.param("docker compose exec clickhouse clickhouse-client -q 'DETACH TABLE updates'", id="ch-detach-table"),
    pytest.param("docker compose exec clickhouse clickhouse-client -q 'ATTACH TABLE updates'", id="ch-attach-table"),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q 'EXCHANGE TABLES a AND b'", id="ch-exchange-tables"
    ),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q 'OPTIMIZE TABLE updates FINAL DEDUPLICATE'",
        id="ch-optimize-deduplicate",
    ),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "REFRESH MATERIALIZED VIEW mv_x"',
        id="pg-refresh-materialized-view",
    ),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "SELECT * INTO stops_copy FROM stops"',
        id="pg-select-into-new-table",
    ),
    # A dev port spelled as a `port=` key: a libpq keyword DSN, a client kwarg,
    # or a CLICKHOUSE_PORT assignment, none of which carry a `:<port>` token.
    pytest.param('psql "host=localhost port=5433 dbname=transit" -c "TRUNCATE agg_x"', id="libpq-keyword-dsn"),
    pytest.param(
        "poetry run python -c \"import psycopg2; psycopg2.connect(host='localhost', port=5433)"
        ".cursor().execute('TRUNCATE agg_x')\"",
        id="python-kwarg-port",
    ),
    pytest.param(
        "CLICKHOUSE_PORT=8123 poetry run python -c \"c.command('TRUNCATE TABLE updates')\"",
        id="clickhouse-port-env-assignment",
    ),
    pytest.param('psql "postgresql://localhost/transit?port=5433" -c "TRUNCATE agg_x"', id="uri-query-port"),
    pytest.param('export PGPORT=5433; psql -c "TRUNCATE agg_x"', id="exported-pgport"),
    pytest.param("bash -c \"PGPORT=5543 psql -c 'TRUNCATE agg_x'\"", id="pgport-inside-shell-string"),
    pytest.param(
        "DATABASE_URL=postgresql://localhost:5544/transit_test?port=5433 make analyze-all",
        id="throwaway-url-overridden-by-port-query",
    ),
    # clickhouse-client's native port, which compose.yml says can be published.
    pytest.param("clickhouse-client --host localhost --port 9000 -q 'TRUNCATE TABLE updates'", id="ch-native-port"),
    # The dev Postgres container as it runs today, created outside compose.
    pytest.param(
        'docker exec transit-pg-latest-main psql -U transit -c "DROP TABLE agencies"', id="current-dev-pg-container"
    ),
    # The client inside the quotes, the feed or the dev target outside them.
    pytest.param(
        "docker exec -i transit-pg sh -c 'psql -U transit' < f.sql", id="nested-client-outer-redirect-container"
    ),
    pytest.param('sh -c "psql -h localhost -p 5433 -U transit" < f.sql', id="nested-client-outer-redirect"),
    pytest.param("cat f.sql | sh -c 'psql -h localhost -p 5433 -U transit'", id="nested-client-outer-pipe"),
    pytest.param("cat f.sql | sh -c 'clickhouse-client --multiquery'", id="nested-ch-client-outer-pipe"),
    pytest.param(
        'bash -c \'psql -h localhost -p 5433 -U transit -c "$1"\' _ "DROP TABLE agencies"',
        id="nested-target-outer-write",
    ),
    # clickhouse-client that only names a host: the port still defaults to the dev one.
    pytest.param("clickhouse-client --host localhost -q 'TRUNCATE TABLE updates'", id="ch-client-loopback-host"),
    pytest.param("clickhouse-client -h 127.0.0.1 < x.sql", id="ch-client-loopback-ip-script"),
    pytest.param("clickhouse-client --host=localhost --queries-file x.sql", id="ch-client-host-equals-script"),
    pytest.param("CLICKHOUSE_HOST=localhost clickhouse-client -q 'DROP TABLE updates'", id="ch-client-host-env"),
    pytest.param("clickhouse-client --host=transit-ch -q 'DROP TABLE updates'", id="ch-client-dev-container-equals"),
    pytest.param("clickhouse-client -htransit-ch -q 'DROP TABLE updates'", id="ch-client-dev-container-glued"),
    pytest.param(
        "clickhouse-client -q 'DROP TABLE updates' # transit-test-ch", id="ch-client-throwaway-name-only-in-a-comment"
    ),
    # curl clusters ending in -F, the value in the next word.
    pytest.param("curl -sF query=@x.sql http://localhost:8123/", id="ch-http-form-cluster-then-value"),
    pytest.param("curl -fsSLF 'query=<x.sql' http://localhost:8123/", id="ch-http-form-long-cluster-then-value"),
    # Substitution readers spelled with a path or `command`; psql's abbreviated --file.
    pytest.param('psql -h localhost -p 5433 -U transit -c "$(/bin/cat fix.sql)"', id="substitution-path-qualified-cat"),
    pytest.param('psql -h localhost -p 5433 -U transit -c "$(command cat fix.sql)"', id="substitution-command-cat"),
    pytest.param("psql -h localhost -p 5433 -U transit --fil=x.sql", id="psql-abbreviated-long-file"),
    # A script read by command substitution reaches psql as one opaque -c argument.
    pytest.param('psql -h localhost -p 5433 -U transit -c "$(cat fix.sql)"', id="psql-c-substitution-cat"),
    pytest.param('psql -h localhost -p 5433 -U transit -c "$(< fix.sql)"', id="psql-c-substitution-redirect"),
    pytest.param("psql -h localhost -p 5433 -U transit -c `cat fix.sql`", id="psql-c-backtick-cat"),
    pytest.param('docker compose exec db psql -U transit -c "$(cat fix.sql)"', id="compose-exec-psql-c-substitution"),
    # A nested shell keeps the target and the file-fed script inside one quoted string.
    pytest.param('sh -c "psql -h localhost -p 5433 -U transit < f.sql"', id="nested-sh-psql-stdin"),
    pytest.param("bash -c 'cat f.sql | psql -h localhost -p 5433 -U transit'", id="nested-bash-piped-psql"),
    pytest.param(
        'bash -c "psql postgresql://transit:transit@localhost:5433/transit -f f.sql"', id="nested-bash-psql-file"
    ),
    pytest.param("sh -c 'clickhouse-client --host transit-ch < f.sql'", id="nested-sh-clickhouse-stdin"),
    # The target outside the quotes, the script inside them.
    pytest.param("docker compose exec db sh -c 'psql -U transit < f.sql'", id="compose-exec-nested-sh-script"),
    # curl and wget uploads of a file to the ClickHouse HTTP port.
    pytest.param("curl -s http://localhost:8123/ -F name=@x.sql", id="ch-http-form-short"),
    pytest.param("curl -s http://localhost:8123/ -Fname=@x.sql", id="ch-http-form-glued"),
    pytest.param("curl -s http://localhost:8123/ --form 'query=@x.sql'", id="ch-http-form-long"),
    pytest.param("curl -s http://localhost:8123/ --form 'query=<x.sql'", id="ch-http-form-content-from-file"),
    pytest.param("curl -s http://localhost:8123/ --data-urlencode query@x.sql", id="ch-http-urlencode-name-file"),
    pytest.param("curl -s http://localhost:8123/ --data-urlencode @x.sql", id="ch-http-urlencode-file"),
    pytest.param("wget -qO- --post-file=x.sql http://localhost:8123/", id="ch-http-wget-post-file-equals"),
    pytest.param("wget -qO- --post-file x.sql http://transit-ch:8123/", id="ch-http-wget-post-file"),
    # clickhouse-client with no host or port connects to the default, i.e. dev, instance.
    pytest.param("clickhouse-client -q 'TRUNCATE TABLE updates'", id="ch-client-bare-truncate"),
    pytest.param('clickhouse-client --query "INSERT INTO updates VALUES (1)"', id="ch-client-bare-insert"),
    pytest.param("clickhouse-client < x.sql", id="ch-client-bare-stdin-script"),
    pytest.param("cat x.sql | clickhouse-client", id="ch-client-bare-piped-script"),
    pytest.param("clickhouse-client --queries-file x.sql", id="ch-client-bare-queries-file"),
    pytest.param("clickhouse client -q 'DROP TABLE updates'", id="ch-client-subcommand-bare-drop"),
    pytest.param(
        "docker exec some-box clickhouse-client -q 'DROP TABLE updates'", id="ch-client-bare-in-unknown-container"
    ),
    pytest.param("bash -c \"clickhouse-client -q 'DROP TABLE updates'\"", id="nested-bare-ch-client"),
    # psql short options glued: -f with its filename, alone or last in a cluster.
    pytest.param("psql -h localhost -p 5433 -U transit -fx.sql", id="psql-f-glued-filename"),
    pytest.param("psql -h localhost -p 5433 -U transit -Atfx.sql", id="psql-f-glued-in-cluster"),
    pytest.param("psql -h localhost -p 5433 -U transit -Atf x.sql", id="psql-f-ends-cluster"),
    # SELECT ... INTO, \copy ... FROM and OPTIMIZE ... DEDUPLICATE with an operator inside the SQL.
    pytest.param(
        'psql -h localhost -p 5433 -U transit -c "SELECT a||b INTO t FROM s"', id="pg-select-into-after-concat-operator"
    ),
    pytest.param(
        'psql -h localhost -p 5433 -U transit -c "SELECT a && b AS x INTO t FROM s"',
        id="pg-select-into-after-overlap-operator",
    ),
    pytest.param(
        'psql -h localhost -p 5433 -U transit -c "\\copy \\"r&d\\" from stdin"', id="copy-from-after-ampersand"
    ),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q "
        "\"OPTIMIZE TABLE updates PARTITION ID 'a&b' FINAL DEDUPLICATE\"",
        id="ch-optimize-deduplicate-after-ampersand",
    ),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c 'SELECT 1' | "
        "psql -h localhost -p 5433 -U transit -c 'SELECT a||b INTO t FROM s'",
        id="select-into-in-the-second-piped-command",
    ),
]

ALLOWED = [
    # An exec into a named service or container that is not a dev one is not the
    # dev store, whatever port the client defaults to inside it.
    pytest.param(
        "docker compose -f deploy/vps/compose.yml exec ml-ch clickhouse-client --query 'INSERT INTO t VALUES (1)'",
        id="ch-client-exec-replica-service",
    ),
    pytest.param(
        "docker exec transit-ml-ml-ch-1 clickhouse-client --query 'INSERT INTO t VALUES (1)'",
        id="ch-client-exec-replica-container",
    ),
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
    # Make's command-line variables beat `.env`, which the Makefile includes.
    pytest.param(
        "make migrate-down CONFIRM=1 DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test",
        id="migrate-down-pointed-at-test-db",
    ),
    # gtfs_pipeline reads no `.env`, so its env prefix is what it runs with.
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test poetry run python gtfs_pipeline.py "
        "migrate down",
        id="cli-migrate-down-pointed-at-test-db",
    ),
    pytest.param("make ch-bootstrap CLICKHOUSE_PORT=8124", id="ch-bootstrap-pointed-at-test-ch"),
    pytest.param(
        "make ingest FOLDER=raw DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test "
        "CLICKHOUSE_PORT=8124",
        id="ingest-pointed-at-both-test-stores",
    ),
    pytest.param(
        "make analyze-all DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test CLICKHOUSE_PORT=8124 "
        "CLICKHOUSE_DATABASE=transit_test",
        id="analyze-all-pointed-at-both-test-stores",
    ),
    pytest.param("psql -h localhost -p 5433 -U transit -c 'SELECT 1' < /dev/null", id="dev-read-with-null-stdin"),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c 'SELECT 1' > out.txt && wc -l < out.txt",
        id="dev-read-redirect-belongs-to-another-command",
    ),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c 'SELECT 1 FROM t WHERE a <> 1 AND b <= 2'",
        id="dev-read-sql-comparison",
    ),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c \"SELECT id FROM t ORDER BY emb <-> '[1,2]' LIMIT 3\"",
        id="dev-read-pgvector-distance",
    ),
    pytest.param("psql -h localhost -p 5544 -U transit transit_test < dump.sql", id="test-db-stdin-script"),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "COPY (SELECT * FROM stops) TO STDOUT"',
        id="sql-copy-to-reads-data-out",
    ),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q \"SELECT * FROM updates INTO OUTFILE '/tmp/u.csv'\"",
        id="ch-select-into-outfile",
    ),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q 'OPTIMIZE TABLE updates'", id="ch-optimize-merges-only"
    ),
    pytest.param('psql "host=localhost port=5433 dbname=transit" -c "SELECT 1"', id="keyword-dsn-read"),
    pytest.param('psql "host=localhost port=5544 dbname=transit_test" -c "TRUNCATE agg_x"', id="keyword-dsn-test-db"),
    pytest.param(
        "CLICKHOUSE_PORT=8124 poetry run python -c \"c.command('TRUNCATE TABLE updates')\"", id="test-ch-port-env"
    ),
    pytest.param('psql "host=localhost sport=5433 dbname=x" -c "TRUNCATE agg_x"', id="port-key-inside-longer-word"),
    pytest.param('psql "host=localhost port=55432 dbname=x" -c "TRUNCATE agg_x"', id="port-key-longer-than-dev-port"),
    pytest.param("make check-aggs", id="make-read-only-target"),
    pytest.param("poetry run python gtfs_pipeline.py check_aggs", id="cli-read-only-subcommand"),
    pytest.param("make test && make lint", id="make-throwaway-and-static-targets"),
    pytest.param('git commit -m "make it faster"', id="quoted-prose-naming-no-target"),
    pytest.param(
        "make prune-query-log DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test>out.log",
        id="test-db-with-a-glued-redirect",
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
    # A read-only substitution or nested shell stays allowed.
    pytest.param("psql -h localhost -p 5433 -U transit -c \"SELECT '$(date +%F)'\"", id="dev-read-date-substitution"),
    pytest.param("sh -c \"psql -h localhost -p 5433 -U transit -c 'SELECT 1'\"", id="nested-sh-dev-read"),
    pytest.param('sh -c "psql -h localhost -p 5544 -U transit < f.sql"', id="nested-sh-test-db-script"),
    pytest.param('psql -h localhost -p 5544 -U transit -c "$(cat fix.sql)"', id="test-db-substitution-script"),
    # HTTP bodies aimed at the throwaway ClickHouse, and harmless requests to the dev one.
    pytest.param("curl -s http://localhost:8124/ -F name=@x.sql", id="test-ch-http-form"),
    pytest.param("wget -qO- --post-file x.sql http://localhost:8124/", id="test-ch-wget-post-file"),
    pytest.param("curl -s 'http://user@localhost:8123/?query=SELECT+1'", id="ch-http-read-with-userinfo"),
    pytest.param("curl -s http://localhost:8123/ --data-urlencode 'query=SELECT 1'", id="ch-http-urlencode-read"),
    pytest.param("curl -s http://localhost:8123/ -F 'name=a@b.example'", id="ch-http-form-literal-value"),
    pytest.param("curl -sf http://localhost:8123/ping", id="ch-http-ping-fail-flag"),
    # clickhouse-client that names a target of its own, or only reads.
    pytest.param("clickhouse-client -q 'SELECT 1'", id="ch-client-bare-read"),
    pytest.param("clickhouse-client --port 8124 -q 'TRUNCATE TABLE updates'", id="ch-client-test-port"),
    pytest.param("clickhouse-client --port=8124 < x.sql", id="ch-client-test-port-equals-script"),
    pytest.param("CLICKHOUSE_PORT=8124 clickhouse-client -q 'DROP TABLE x'", id="ch-client-test-port-env"),
    pytest.param(
        "docker exec transit-test-ch clickhouse-client -q 'TRUNCATE TABLE updates'", id="ch-client-test-container"
    ),
    pytest.param("clickhouse-client --host ch.example.net -q 'TRUNCATE TABLE t'", id="ch-client-explicit-other-host"),
    # psql option clusters that are not -f.
    pytest.param("psql -h localhost -p 5433 -U transit -Atc 'SELECT 1'", id="psql-cluster-read"),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c 'SELECT 1' | grep -f patterns.txt", id="dev-read-piped-grep-f"
    ),
    pytest.param("psql -h localhost -p 5433 -U transit -Fx.sql -c 'SELECT 1'", id="psql-field-separator-glued"),
    # A shell pipeline or list beside a read is not part of the SQL.
    pytest.param("psql -h localhost -p 5433 -U transit -c 'SELECT 1' | tee into.txt", id="dev-read-piped-to-tee-into"),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c 'SELECT 1' && echo wrote it into the log", id="dev-read-and-list-into"
    ),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c 'SELECT 1' & echo started into the background",
        id="dev-read-background-into",
    ),
    pytest.param(
        "psql -h localhost -p 5433 -U transit -c 'SELECT 1' | xargs -I{} echo {} && echo copy from here",
        id="dev-read-pipeline-then-copy-from-prose",
    ),
    pytest.param('psql -h localhost -p 5433 -U transit -c "SELECT a||b FROM s" | sort', id="dev-read-concat-operator"),
    pytest.param('psql -h localhost -p 5433 -U transit -c "SELECT a && b FROM s"', id="dev-read-overlap-operator"),
    pytest.param(
        "docker compose exec clickhouse clickhouse-client -q 'OPTIMIZE TABLE updates' | grep deduplicate",
        id="ch-optimize-merges-only-piped-to-grep",
    ),
    pytest.param("clickhouse-client -f CSV -q 'SELECT 1'", id="ch-client-format-short-flag"),
    pytest.param("psql -h localhost -p 5433 -U transit -A -F , -c 'SELECT 1'", id="psql-separate-field-separator"),
    pytest.param(
        r"psql -h localhost -p 5433 -U transit -c '\copy (select a||b from t) to out.csv'",
        id="copy-query-to-reads-data-out",
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
