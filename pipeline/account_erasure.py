"""Hard deletion of one user and the personal data tied to them.

The FK cascade from ``users`` removes most of it; ``erase_user`` first
removes what the cascade cannot reach, then deletes the row. ``USER_FKS`` is
the decision for every FK to ``users``: ``tests/db/test_account_erasure.py``
holds it to the live schema, so a new FK cannot land without one.
"""

from __future__ import annotations

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
