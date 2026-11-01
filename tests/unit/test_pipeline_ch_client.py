"""The pipeline's ClickHouse client survives a long scan without poisoning the next query."""

import pipeline.clickhouse as ch


def _captured_kwargs(monkeypatch) -> dict:
    seen: dict = {}

    def fake_get_client(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("CLICKHOUSE_DATABASE", "d")
    monkeypatch.setattr(ch.clickhouse_connect, "get_client", fake_get_client)
    ch.get_client()
    return seen


def test_queries_share_no_server_session(monkeypatch):
    # A timed-out query keeps running on the server and holds its session, so
    # a shared session would refuse every later query (SESSION_IS_LOCKED).
    assert _captured_kwargs(monkeypatch)["autogenerate_session_id"] is False


def test_waits_long_enough_for_a_full_rebuild_scan(monkeypatch):
    assert _captured_kwargs(monkeypatch)["send_receive_timeout"] >= 1800
