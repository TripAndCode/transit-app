"""Tests for ``pipeline.audit.record_event``: one INSERT into ``login_events``."""

import json

import pytest

from pipeline.audit import record_event


@pytest.mark.asyncio
async def test_record_event_writes_row(aconn):
    uid = (await aconn.fetchrow("INSERT INTO users (email) VALUES ('a@x') RETURNING user_id"))["user_id"]
    await record_event(
        aconn,
        user_id=uid,
        actor_id=uid,
        kind="login",
        provider="google",
        ip="1.2.3.4",
        user_agent="ua",
        meta={"foo": "bar"},
    )
    row = await aconn.fetchrow("SELECT * FROM login_events WHERE user_id=$1", uid)
    assert row["kind"] == "login"
    assert row["provider"] == "google"
    assert json.loads(row["meta"]) == {"foo": "bar"}
    assert str(row["ip"]) == "1.2.3.4"


@pytest.mark.asyncio
async def test_record_event_stores_an_unparsable_address_as_null(aconn):
    """The action being recorded must not fail because its client address
    would not cast to ``inet``."""
    uid = (await aconn.fetchrow("INSERT INTO users (email) VALUES ('b@x') RETURNING user_id"))["user_id"]
    await record_event(aconn, user_id=uid, actor_id=uid, kind="login", ip="testclient", user_agent="ua")
    row = await aconn.fetchrow("SELECT ip, user_agent FROM login_events WHERE user_id=$1", uid)
    assert row["ip"] is None
    assert row["user_agent"] == "ua"
