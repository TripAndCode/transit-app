"""The test-DB redirect must refuse a dev Postgres port, not rename its database."""

import os

import psycopg2
import pytest

from tests.conftest import _redirect_to_test_db


@pytest.fixture
def connect_calls(monkeypatch):
    """Record any connection attempt instead of making it."""
    calls: list[str] = []

    def fake_connect(dsn, *args, **kwargs):
        calls.append(dsn)
        raise psycopg2.OperationalError("connection blocked by test")

    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    for var in ("TEST_DATABASE_URL", "PGPORT", "PGHOST"):
        monkeypatch.delenv(var, raising=False)
    return calls


@pytest.mark.parametrize("port", [5433, 5543])
def test_dev_port_is_refused_before_any_connection(monkeypatch, connect_calls, port):
    monkeypatch.setenv("DATABASE_URL", f"postgresql://transit:transit@localhost:{port}/transit")

    with pytest.raises(pytest.UsageError, match="dev Postgres port"):
        _redirect_to_test_db()

    assert connect_calls == []


@pytest.mark.parametrize("port", [5433, 5543])
def test_explicit_test_database_url_on_a_dev_port_is_refused(monkeypatch, connect_calls, port):
    monkeypatch.setenv("DATABASE_URL", "postgresql://transit:transit@localhost:5544/transit")
    monkeypatch.setenv("TEST_DATABASE_URL", f"postgresql://transit:transit@localhost:{port}/transit_test")

    with pytest.raises(pytest.UsageError, match="TEST_DATABASE_URL points at the dev Postgres port"):
        _redirect_to_test_db()

    assert connect_calls == []


@pytest.mark.parametrize("port", [5433, 5543])
def test_dev_port_in_the_query_string_is_refused(monkeypatch, connect_calls, port):
    monkeypatch.setenv("DATABASE_URL", f"postgresql://transit:transit@localhost/transit?port={port}")

    with pytest.raises(pytest.UsageError, match="dev Postgres port"):
        _redirect_to_test_db()

    assert connect_calls == []


@pytest.mark.parametrize("port", [5433, 5543])
def test_explicit_test_database_url_with_a_dev_query_port_is_refused(monkeypatch, connect_calls, port):
    monkeypatch.setenv("DATABASE_URL", "postgresql://transit:transit@localhost:5544/transit")
    monkeypatch.setenv("TEST_DATABASE_URL", f"postgresql://transit:transit@localhost:5544/transit_test?port={port}")

    with pytest.raises(pytest.UsageError, match="TEST_DATABASE_URL points at the dev Postgres port"):
        _redirect_to_test_db()

    assert connect_calls == []


@pytest.mark.parametrize("port", [5433, 5543])
def test_dev_pgport_under_a_port_less_url_is_refused(monkeypatch, connect_calls, port):
    monkeypatch.setenv("DATABASE_URL", "postgresql://transit:transit@localhost/transit")
    monkeypatch.setenv("PGPORT", str(port))

    with pytest.raises(pytest.UsageError, match="dev Postgres port"):
        _redirect_to_test_db()

    assert connect_calls == []


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://transit:transit@localhost:54x3/transit",
        "postgresql://transit:transit@localhost/transit?port=abc",
    ],
)
def test_unreadable_port_is_refused(monkeypatch, connect_calls, url):
    monkeypatch.setenv("DATABASE_URL", url)

    with pytest.raises(pytest.UsageError, match="cannot read"):
        _redirect_to_test_db()

    assert connect_calls == []


def test_throwaway_port_is_still_redirected_to_the_sibling_database(monkeypatch, connect_calls):
    monkeypatch.setenv("DATABASE_URL", "postgresql://transit:transit@localhost:5544/transit")

    _redirect_to_test_db()

    assert os.environ["DATABASE_URL"] == "postgresql://transit:transit@localhost:5544/transit_test"
    assert connect_calls == ["postgresql://transit:transit@localhost:5544/postgres"]


def test_an_exported_dev_pgport_does_not_block_a_url_that_names_its_port(monkeypatch, connect_calls):
    monkeypatch.setenv("DATABASE_URL", "postgresql://transit:transit@localhost:5544/transit")
    monkeypatch.setenv("PGPORT", "5433")

    _redirect_to_test_db()

    assert os.environ["DATABASE_URL"] == "postgresql://transit:transit@localhost:5544/transit_test"
