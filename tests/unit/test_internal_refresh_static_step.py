"""The operator's reload step closes what it opened, whichever close or open fails."""

from unittest.mock import MagicMock

import psycopg2
import pytest

from api.routers import internal


def test_a_clickhouse_client_that_fails_to_open_still_closes_the_postgres_connection(monkeypatch):
    conn = MagicMock()
    monkeypatch.setattr(psycopg2, "connect", lambda url: conn)

    def down():
        raise RuntimeError("clickhouse down")

    monkeypatch.setattr("pipeline.clickhouse.get_client", down)
    with pytest.raises(RuntimeError, match="clickhouse down"):
        internal._refresh_static_step("postgresql://test", 7, requested_by=None, parent_run_id=None)
    conn.close.assert_called_once()


def test_a_clickhouse_close_that_fails_still_closes_the_postgres_connection(monkeypatch):
    conn = MagicMock()
    ch = MagicMock()
    ch.close.side_effect = RuntimeError("close failed")
    monkeypatch.setattr(psycopg2, "connect", lambda url: conn)
    monkeypatch.setattr("pipeline.clickhouse.get_client", lambda: ch)
    monkeypatch.setattr("pipeline.static_fetcher.refresh_static_recorded", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="close failed"):
        internal._refresh_static_step("postgresql://test", 7, requested_by=None, parent_run_id=None)
    conn.close.assert_called_once()
