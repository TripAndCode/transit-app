"""Hard deletion of one user and the personal data tied to them.

The FK cascade from ``users`` removes most of it; ``erase_user`` first
removes what the cascade cannot reach, then deletes the row. ``USER_FKS`` is
the decision for every FK to ``users``: ``tests/db/test_account_erasure.py``
holds it to the live schema, so a new FK cannot land without one.
"""

from __future__ import annotations

import asyncpg

from pipeline.audit import record_event

#: What deleting a user does to each FK column that references it. The
#: SET NULL columns whose rows are the user's own personal data are deleted
#: or cleared by ``erase_user`` before the row goes; the rest (the user's
#: authorship of an operational row) are left to the SET NULL.
USER_FKS: dict[tuple[str, str], str] = {
    ("sessions", "user_id"): "CASCADE",
    ("oauth_identities", "user_id"): "CASCADE",
    ("filter_presets", "user_id"): "CASCADE",
    ("user_llm_keys", "user_id"): "CASCADE",
    ("user_activity_daily", "user_id"): "CASCADE",
    ("ask_conversations", "user_id"): "CASCADE",
    ("login_events", "user_id"): "SET NULL",
    ("login_events", "actor_id"): "SET NULL",
    ("api_keys", "owner_user_id"): "SET NULL",
    ("user_invites", "invited_by"): "SET NULL",
    ("user_invites", "consumed_user_id"): "SET NULL",
    ("admin_audit", "actor_id"): "SET NULL",
    ("feature_flags", "updated_by"): "SET NULL",
    ("pipeline_runs", "requested_by"): "SET NULL",
}


class ErasureRefused(Exception):
    """The account may not be erased; ``reason`` is the API's error detail."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# JSON-string replacement rather than key-by-key edits, so the value is found
# however deeply or in whatever array shape an audit snapshot stored it.
_MASK_AUDIT_EMAIL = """
    UPDATE admin_audit SET
        before = replace(before::text, to_jsonb($1::text)::text, '"[deleted]"')::jsonb,
        after = replace(after::text, to_jsonb($1::text)::text, '"[deleted]"')::jsonb
    WHERE strpos(coalesce(before::text, '') || coalesce(after::text, ''), to_jsonb($1::text)::text) > 0
"""

# Names are not unique, so they are masked only where the row is about this user.
_MASK_AUDIT_NAME = """
    UPDATE admin_audit SET
        before = replace(before::text, to_jsonb($1::text)::text, '"[deleted]"')::jsonb,
        after = replace(after::text, to_jsonb($1::text)::text, '"[deleted]"')::jsonb
    WHERE target_type = 'user' AND $2::text = ANY (string_to_array(target_id, ','))
"""


async def erase_user(conn: asyncpg.Connection, user_id: int) -> None:
    """Delete ``user_id`` and their personal data. Run inside ``conn.transaction()``.

    Every active admin row is locked in user_id order, as the admin router's
    last-admin guard does, so two admins deleting themselves at once cannot
    both pass the check.
    """
    rows = await conn.fetch(
        """
        SELECT user_id, email, name, role, suspended_at, password_hash FROM users
        WHERE user_id = $1 OR (role = 'admin' AND suspended_at IS NULL)
        ORDER BY user_id
        FOR UPDATE
        """,
        user_id,
    )
    target = next((r for r in rows if r["user_id"] == user_id), None)
    if target is None:
        raise ErasureRefused("not_found")
    if target["password_hash"] is not None:
        raise ErasureRefused("managed_account")
    other_admins = sum(1 for r in rows if r["user_id"] != user_id)
    if target["role"] == "admin" and target["suspended_at"] is None and other_admins == 0:
        raise ErasureRefused("last_admin")

    email, name = target["email"], target["name"]
    await conn.execute("DELETE FROM login_events WHERE user_id = $1", user_id)
    await conn.execute("UPDATE login_events SET ip = NULL, user_agent = NULL WHERE actor_id = $1", user_id)
    await conn.execute(
        "DELETE FROM api_keys WHERE owner_user_id = $1 OR (owner_user_id IS NULL AND lower(owner_email) = lower($2))",
        user_id,
        email,
    )
    await conn.execute(
        "DELETE FROM user_invites WHERE consumed_user_id = $1 OR lower(email) = lower($2)", user_id, email
    )
    await conn.execute(_MASK_AUDIT_EMAIL, email)
    if name:
        await conn.execute(_MASK_AUDIT_NAME, name, str(user_id))
    await conn.execute("UPDATE admin_audit SET ip = NULL WHERE actor_id = $1", user_id)
    await conn.execute("DELETE FROM users WHERE user_id = $1", user_id)
    # Counts deletions without identifying anyone.
    await record_event(conn, user_id=None, kind="deleted", meta={"self": True})
