"""`gtfs_pipeline.py schema-up`: Postgres migrations, then the ClickHouse
schema, then the release record, and nothing after a step that failed."""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


def _run(calls: list[str], migrate_effect=None, clickhouse_effect=None) -> MagicMock:
    client = MagicMock()

    def migrate(args):
        calls.append(f"migrate {args.direction} {args.allow_destructive_up}")
        if migrate_effect is not None:
            raise migrate_effect
        return ["0114"]

    def clickhouse(_client):
        calls.append("clickhouse")
        if clickhouse_effect is not None:
            raise clickhouse_effect

    def record(_connect, sha, applied):
        calls.append(f"record {sha} {applied}")

    with (
        patch.object(gtfs_pipeline, "cmd_migrate", side_effect=migrate),
        patch("pipeline.clickhouse.get_client", return_value=client),
        patch("db.clickhouse.bootstrap.apply_schema", side_effect=clickhouse),
        patch("pipeline.deploys.release_sha", return_value="abc1234"),
        patch("pipeline.deploys.record_deploy", side_effect=record),
    ):
        gtfs_pipeline.cmd_schema_up(Namespace(allow_destructive_up=["0107"]))
    return client


def test_postgres_migrates_then_clickhouse_then_the_release_is_recorded():
    calls: list[str] = []
    client = _run(calls)
    assert calls == ["migrate up ['0107']", "clickhouse", "record abc1234 ['0114']"]
    client.close.assert_called_once()


def test_a_refused_migration_leaves_clickhouse_and_the_record_alone():
    calls: list[str] = []
    with pytest.raises(SystemExit):
        _run(calls, migrate_effect=SystemExit("migrate up: refused"))
    assert calls == ["migrate up ['0107']"]


def test_a_failed_clickhouse_schema_records_no_release():
    calls: list[str] = []
    with pytest.raises(RuntimeError):
        _run(calls, clickhouse_effect=RuntimeError("clickhouse down"))
    assert calls == ["migrate up ['0107']", "clickhouse"]
