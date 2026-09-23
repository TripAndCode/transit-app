"""`api.admin_audit.record_admin_action` — the call sites' contract.

The body is a logging stub until the `admin_audit` table lands, so what is
pinned here is the signature every admin mutation is written against plus the
one safety property the stub must not lose when the body is replaced: the
values inside `before`/`after` are user PII and never reach the log line.
"""

from __future__ import annotations

import logging

import pytest

from api.admin_audit import record_admin_action


class _Conn:
    """Stand-in for the asyncpg connection the real implementation will use.
    Any access at all would mean the stub started touching the DB."""

    def __getattr__(self, name):  # pragma: no cover - only reached on a regression
        raise AssertionError(f"the audit stub must not call conn.{name}")


async def test_records_an_action_without_touching_the_connection(caplog):
    with caplog.at_level(logging.INFO, logger="api.admin_audit"):
        result = await record_admin_action(
            _Conn(),
            actor_id=1,
            action="user.llm_approved",
            target_type="user",
            target_id="42",
            before={"llm_approved": False},
            after={"llm_approved": True},
            reason="approved in the morning review",
        )
    assert result is None
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "user.llm_approved" in message
    assert "user" in message
    assert "42" in message


async def test_changed_field_names_are_logged_but_their_values_are_not(caplog):
    with caplog.at_level(logging.INFO, logger="api.admin_audit"):
        await record_admin_action(
            _Conn(),
            actor_id=1,
            action="user.delete",
            target_type="user",
            target_id="42",
            before={"email": "person@example.com"},
            after={"email": "deleted-42@local"},
        )
    message = caplog.records[0].getMessage()
    assert "email" in message
    assert "person@example.com" not in message
    assert "deleted-42@local" not in message


async def test_a_replaced_policy_table_logs_its_columns_and_none_of_its_cells(caplog):
    """Surfaces that swap a whole table pass the rows, not one row's columns.
    The log still has to come out as field names only."""
    with caplog.at_level(logging.INFO, logger="api.admin_audit"):
        await record_admin_action(
            _Conn(),
            actor_id=1,
            action="agency_standards_updated",
            target_type="agency",
            target_id=7,
            before=[{"route_code": "R1", "bonus_malus_rate": 0.5}],
            after=[{"route_code": "R2", "target_seconds": 90}],
        )
    message = caplog.records[0].getMessage()
    assert "route_code" in message
    assert "bonus_malus_rate" in message
    assert "target_seconds" in message
    for value in ("R1", "R2", "0.5", "90"):
        assert value not in message


async def test_optional_arguments_may_be_omitted(caplog):
    with caplog.at_level(logging.INFO, logger="api.admin_audit"):
        await record_admin_action(_Conn(), actor_id=1, action="board.refresh", target_type="system", target_id=None)
    assert len(caplog.records) == 1


async def test_every_argument_after_the_connection_is_keyword_only():
    with pytest.raises(TypeError):
        await record_admin_action(_Conn(), 1, "user.delete", "user", "42")  # type: ignore[misc]
