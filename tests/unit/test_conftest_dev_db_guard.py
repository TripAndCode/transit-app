"""The test-DB redirect must refuse a dev Postgres port, not rename its database."""

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
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    return calls


@pytest.mark.parametrize("port", [5433, 5543])
def test_dev_port_is_refused_before_any_connection(monkeypatch, connect_calls, port):
    monkeypatch.setenv("DATABASE_URL", f"postgresql://transit:transit@localhost:{port}/transit")

    with pytest.raises(pytest.UsageError, match="dev Postgres port"):
        _redirect_to_test_db()

    assert connect_calls == []


def test_throwaway_port_is_still_redirected_to_the_sibling_database(monkeypatch, connect_calls):
    monkeypatch.setenv("DATABASE_URL", "postgresql://transit:transit@localhost:5544/transit")

    _redirect_to_test_db()

    import os

    assert os.environ["DATABASE_URL"] == "postgresql://transit:transit@localhost:5544/transit_test"
    assert connect_calls == ["postgresql://transit:transit@localhost:5544/postgres"]
