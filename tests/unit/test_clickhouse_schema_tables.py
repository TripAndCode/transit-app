"""db/clickhouse/schema.sql creates exactly the ClickHouse tables the app reads or writes."""

import re
from pathlib import Path

from pipeline import ch_aggregates, static_archive
from pipeline.clickhouse import LIVE_TABLE, UPDATES_TABLE

SCHEMA = Path(__file__).resolve().parents[2] / "db" / "clickhouse" / "schema.sql"


def test_the_schema_creates_only_the_tables_the_app_uses():
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA.read_text()))
    used = {UPDATES_TABLE, LIVE_TABLE, *ch_aggregates.ALL_TABLES, static_archive.ARCHIVE, static_archive.VERSION_MAP}
    assert created == used
