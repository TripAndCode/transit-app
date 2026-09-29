"""Hard deletion of a user (`pipeline/account_erasure.py`)."""

import pytest

from pipeline.account_erasure import USER_FKS

_DELETE_RULES = {"c": "CASCADE", "n": "SET NULL", "a": "NO ACTION", "r": "RESTRICT", "d": "SET DEFAULT"}


@pytest.mark.asyncio
async def test_every_foreign_key_to_users_has_a_deletion_decision(aconn):
    """A new FK to users fails here until USER_FKS says what deleting a user does to it."""
    rows = await aconn.fetch(
        """
        SELECT cl.relname AS table_name, a.attname AS column_name, c.confdeltype::text AS rule
        FROM pg_constraint c
        JOIN pg_class cl ON cl.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'f' AND c.confrelid = 'users'::regclass
        """
    )
    actual = {(r["table_name"], r["column_name"]): _DELETE_RULES[r["rule"]] for r in rows}
    assert actual == USER_FKS
