"""Tests that need the throwaway ClickHouse (`make ch-test`, RUN_CH_INTEGRATION=1)
and nothing else. Each test or module carries its own skip; the root
conftest's `ch_client`/`_ch_schema` fixtures do the connecting."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    """No-op: nothing here touches Postgres, so no migration run is wanted."""
