"""pipeline.deploys: which commit a release names, and that recording it
never fails the deploy."""

import logging
from unittest.mock import MagicMock

import pytest

from pipeline.deploys import record_deploy, release_sha

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"GIT_SHA": SHA}, SHA),
        ({"GIT_SHA": f"  {SHA.upper()}\n"}, SHA),
        ({"GIT_SHA": "abc1234"}, "abc1234"),
        ({"GIT_SHA": "", "RAILWAY_GIT_COMMIT_SHA": SHA}, SHA),
        ({"GIT_SHA": "abc1234", "RAILWAY_GIT_COMMIT_SHA": SHA}, "abc1234"),
        ({}, None),
        ({"GIT_SHA": ""}, None),
    ],
    ids=["full", "case-and-space", "short", "railway-fallback", "build-arg-first", "unset", "empty"],
)
def test_release_sha(environ, expected):
    assert release_sha(environ) == expected


@pytest.mark.parametrize(
    "value", ["main", "abc123", "g123456", "0123456789abcdef0123456789abcdef012345678", "abc 1234"]
)
def test_a_value_that_is_not_a_commit_hash_is_recorded_as_none(value, caplog):
    with caplog.at_level(logging.WARNING, logger="pipeline.deploys"):
        assert release_sha({"GIT_SHA": value, "RAILWAY_GIT_COMMIT_SHA": SHA}) is None
    assert "GIT_SHA is not a commit hash" in caplog.text


def test_a_connection_that_fails_costs_only_the_row(caplog):
    def connect():
        raise OSError("connection refused")

    with caplog.at_level(logging.WARNING, logger="pipeline.deploys"):
        record_deploy(connect, SHA, ["0114"])
    assert "the release was not recorded" in caplog.text


def test_an_insert_that_fails_costs_only_the_row_and_closes_the_connection(caplog):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("relation does not exist")

    with caplog.at_level(logging.WARNING, logger="pipeline.deploys"):
        record_deploy(lambda: conn, SHA, [])
    assert "the release was not recorded" in caplog.text
    conn.commit.assert_not_called()
    conn.close.assert_called_once()
