import os

import clickhouse_connect
import pytest


def _ch_test_client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.mark.skipif(
    os.environ.get("RUN_CH_INTEGRATION") != "1",
    reason="requires `make ch-test` running",
)
def test_clickhouse_reachable():
    client = _ch_test_client()
    result = client.query("SELECT 1")
    assert result.result_rows == [(1,)]
    client.close()
