"""`candidate_ports` must name every port either driver could connect a URL to.

The expectations follow libpq (`psycopg2.extensions.parse_dsn`) and asyncpg's DSN
parser (`asyncpg.connect_utils`); the guard holds only while it agrees with both.
"""

import pytest

from tests.postgres_ports import candidate_ports

DEV = [5433, 5543]


@pytest.mark.parametrize("port", DEV)
@pytest.mark.parametrize(
    ("url", "env"),
    [
        pytest.param("postgresql://transit:transit@localhost/transit?port={p}", {}, id="query-port"),
        pytest.param("postgresql://localhost/transit?port=0{p}", {}, id="zero-padded-query-port"),
        pytest.param("postgresql://localhost/transit?port=%20{p}%20", {}, id="whitespace-padded-query-port"),
        pytest.param("postgresql://localhost:5544/transit_test?port={p}", {}, id="libpq-query-overrides-authority"),
        pytest.param("postgresql://localhost:{p}/transit_test?port=5544", {}, id="asyncpg-authority-beats-query"),
        pytest.param("postgresql://localhost/transit?port=5544&port={p}", {}, id="last-query-port-wins"),
        pytest.param("postgresql:///transit?port={p}", {}, id="host-less-query-port"),
        pytest.param("postgresql://localhost:0{p}/transit", {}, id="zero-padded-authority-port"),
        pytest.param("postgresql://h1:5544,h2:{p}/transit", {}, id="second-host-of-a-list"),
        pytest.param("postgresql://[::1]:{p}/transit", {}, id="ipv6-authority"),
        pytest.param("host=localhost port={p} dbname=transit", {}, id="keyword-dsn"),
        pytest.param("postgresql://localhost/transit", {"PGPORT": "{p}"}, id="pgport-port-less-url"),
        pytest.param("postgresql:///transit", {"PGPORT": "{p}"}, id="pgport-host-less-url"),
        pytest.param("postgresql://localhost/transit?port=", {"PGPORT": "{p}"}, id="pgport-empty-query-port"),
        pytest.param(
            "postgresql://localhost/transit?port=5544", {"PGPORT": "{p}"}, id="asyncpg-pgport-for-a-bare-host"
        ),
        pytest.param("postgresql://h1:5544,h2/transit", {"PGPORT": "{p}"}, id="pgport-for-one-host-of-a-list"),
        pytest.param("postgresql:///transit?host=/tmp", {"PGPORT": "{p}"}, id="pgport-unix-socket"),
        pytest.param("postgresql:///transit?host=localhost:{p}", {}, id="asyncpg-port-in-host-query"),
        pytest.param("postgresql:///transit", {"PGHOST": "localhost:{p}"}, id="asyncpg-port-in-pghost"),
    ],
)
def test_reaches_the_dev_port(url, env, port):
    assert port in candidate_ports(url.format(p=port), {k: v.format(p=port) for k, v in env.items()})


def test_percent_encoded_authority_port_is_decoded():
    assert 5433 in candidate_ports("postgresql://localhost:%35433/transit", {})


@pytest.mark.parametrize(
    ("url", "env", "expected"),
    [
        pytest.param("postgresql://transit:transit@localhost:5544/transit", {}, {5544}, id="throwaway"),
        pytest.param(
            "postgresql://localhost:5544/transit", {"PGPORT": "5433"}, {5544}, id="authority-port-beats-pgport"
        ),
        pytest.param("postgresql:///transit?port=5544", {"PGPORT": "5433"}, {5544}, id="query-port-beats-pgport"),
        pytest.param(
            "postgresql://localhost:5544/transit", {"PGHOST": "localhost:5433"}, {5544}, id="url-host-beats-pghost"
        ),
        pytest.param("postgresql://localhost/transit", {}, {5432}, id="default-port"),
        pytest.param("postgresql://localhost:55433/transit", {}, {55433}, id="longer-port-is-not-a-dev-port"),
        pytest.param("postgresql://localhost:15433/transit", {}, {15433}, id="port-is-compared-as-a-number"),
    ],
)
def test_ports_a_driver_would_use(url, env, expected):
    assert candidate_ports(url, env) == expected


@pytest.mark.parametrize(
    ("url", "env"),
    [
        pytest.param("postgresql://localhost:54x3/transit", {}, id="authority-port"),
        pytest.param("postgresql://localhost/transit?port=abc", {}, id="query-port"),
        pytest.param("postgresql://localhost/transit", {"PGPORT": "abc"}, id="pgport"),
        pytest.param("postgresql:///transit", {"PGHOST": "localhost:abc"}, id="pghost-port"),
        pytest.param("postgresql://localhost/transit?nonsense=1", {}, id="libpq-rejects-the-url"),
    ],
)
def test_an_unreadable_port_raises(url, env):
    with pytest.raises(ValueError):
        candidate_ports(url, env)
