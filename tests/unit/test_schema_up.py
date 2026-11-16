"""`gtfs_pipeline.py schema-up`: Postgres migrations, then the ClickHouse
schema, and nothing after a refused migration."""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


def _run(migrate_effect=None):
    calls: list[str] = []
    client = MagicMock()

    def migrate(args):
        calls.append(f"migrate {args.direction} {args.allow_destructive_up}")
        if migrate_effect is not None:
            raise migrate_effect

    with (
        patch.object(gtfs_pipeline, "cmd_migrate", side_effect=migrate),
        patch("pipeline.clickhouse.get_client", return_value=client),
        patch("db.clickhouse.bootstrap.apply_schema", side_effect=lambda c: calls.append("clickhouse")),
    ):
        gtfs_pipeline.cmd_schema_up(Namespace(allow_destructive_up=["0107"]))
    return calls, client


def test_postgres_migrates_before_the_clickhouse_schema():
    calls, client = _run()
    assert calls == ["migrate up ['0107']", "clickhouse"]
    client.close.assert_called_once()


def test_a_refused_migration_leaves_clickhouse_alone():
    with pytest.raises(SystemExit):
        _run(SystemExit("migrate up: refused"))
