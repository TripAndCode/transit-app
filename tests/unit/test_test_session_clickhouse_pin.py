"""A client the code under test builds from the environment must reach the
same throwaway ClickHouse the fixtures seed, whatever the shell exported."""

from pipeline.clickhouse import ch_conn_kwargs
from tests.conftest import _CH_TEST, _pin_clickhouse_to_test_instance


def _fixture_server():
    return {**_CH_TEST, "port": int(_CH_TEST["port"]), "secure": False}


def test_the_session_starts_pinned():
    assert ch_conn_kwargs() == _fixture_server()


def test_an_inherited_dev_block_is_overridden(monkeypatch):
    for name, value in {
        "CLICKHOUSE_HOST": "dev.example",
        "CLICKHOUSE_PORT": "8123",
        "CLICKHOUSE_USER": "dev",
        "CLICKHOUSE_PASSWORD": "dev",
        "CLICKHOUSE_DATABASE": "transit",
        "CLICKHOUSE_SECURE": "true",
    }.items():
        monkeypatch.setenv(name, value)

    _pin_clickhouse_to_test_instance()

    assert ch_conn_kwargs() == _fixture_server()
