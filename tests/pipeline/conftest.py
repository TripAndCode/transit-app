import os

import clickhouse_connect
import pytest

from db.clickhouse.bootstrap import apply_schema


def _ch_test_client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.fixture
def ch_client():
    """ClickHouse client against the throwaway `make ch-test` instance.

    Mirrors tests/unit/test_pipeline_clickhouse.py's fixture (Task 4): drop +
    reapply the schema before each test for isolation, since ClickHouse has
    no transactional rollback to lean on like the pg_conn fixture does.
    Shared here (rather than duplicated per-file) because both
    tests/pipeline/test_ingest.py and tests/pipeline/test_freshness.py need
    it. The skip (rather than a file-level pytestmark) lives here so pure,
    DB-free tests in the same modules (e.g. test_parse_trip_id_*) still run
    without `make ch-test` — only tests that actually request this fixture
    are gated behind RUN_CH_INTEGRATION, same as Task 4.
    """
    if os.environ.get("RUN_CH_INTEGRATION") != "1":
        pytest.skip("requires `make ch-test` (RUN_CH_INTEGRATION=1)")
    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    yield client
    client.close()
