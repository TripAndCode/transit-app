"""Retention for the personal data the privacy policy states a period for.

Per-user usage counts and login history are kept for
``PERSONAL_DATA_RETENTION_MONTHS``; a session row lives no longer than its
own expiry. The API process runs these deletes daily (``api/retention.py``)
rather than leaving them to an external scheduler, so the stated periods
hold wherever the app runs; ``gtfs_pipeline.py prune-personal-data`` runs the
same statements by hand.
"""

PERSONAL_DATA_RETENTION_MONTHS = 25


def personal_data_prune_sql(months: int) -> list[str]:
    """The DELETE statements, in the order they run.

    The interval is embedded as text because asyncpg has no placeholder for an
    INTERVAL literal; ``int()`` here is what makes that safe. Usage days are
    Asia/Tokyo calendar days, so their cutoff is too.
    """
    m = int(months)
    return [
        f"DELETE FROM user_activity_daily WHERE day < (now() AT TIME ZONE 'Asia/Tokyo')::date - INTERVAL '{m} months'",
        f"DELETE FROM login_events WHERE created_at < now() - INTERVAL '{m} months'",
        "DELETE FROM sessions WHERE expires_at < now()",
    ]
