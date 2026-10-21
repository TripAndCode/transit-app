"""Retention windows and the DELETE text that enforces them.

Every period the privacy policy states -- per-user usage counts and login
history (``PERSONAL_DATA_RETENTION_MONTHS``), sessions past their expiry, the
unlinked Ask questions (the question log and the intent cache's question
text) and the admin audit log -- is pruned daily by the API process
(``api/retention.py`` via ``policy_prune_sql``), so the stated periods hold
without an external scheduler. Most windows can also be pruned by hand with
their `gtfs_pipeline.py` command.

Intervals are embedded as text rather than bound as parameters: asyncpg has
no placeholder for an INTERVAL literal. Each builder applies ``int()`` itself
rather than trusting the annotation, because that coercion is what makes the
interpolation safe.
"""

QUERY_LOG_RETENTION_DAYS = 90

#: The control board reads pipeline_runs one JST day at a time (see
#: api/admin_runs.py); a run this far back has no viewer left to show it to.
PIPELINE_RUNS_RETENTION_DAYS = 90

#: Matches the deploy's own 400-day data-retention horizon (RETENTION_DAYS in
#: docs/deploy-railway.md), so the audit trail never outlives the operational
#: data it explains changes to.
ADMIN_AUDIT_RETENTION_DAYS = 400


def prune_pipeline_runs_sql(days: int) -> str:
    """DELETE text for the `pipeline_runs` retention prune."""
    return f"DELETE FROM pipeline_runs WHERE started_at < now() - INTERVAL '{int(days)} days'"


def prune_admin_audit_sql(days: int) -> str:
    """DELETE text for the `admin_audit` retention prune."""
    return f"DELETE FROM admin_audit WHERE at < now() - INTERVAL '{int(days)} days'"


def prune_query_log_sql(days: int) -> str:
    """DELETE text for the `ask_query_log` retention prune."""
    return f"DELETE FROM ask_query_log WHERE created_at < now() - INTERVAL '{int(days)} days'"


def prune_intent_cache_sql(days: int) -> str:
    """DELETE text for the `ask_intent_cache` retention prune. A promoted row
    stays: its `rag_chunks` suggestion resolves its tool and args from it."""
    cutoff = f"now() - INTERVAL '{int(days)} days'"
    return f"DELETE FROM ask_intent_cache WHERE last_used_at < {cutoff} AND promoted_at IS NULL"


PERSONAL_DATA_RETENTION_MONTHS = 25


def personal_data_prune_sql(months: int) -> list[str]:
    """The personal-data DELETE statements, in the order they run. Usage days
    are Asia/Tokyo calendar days, so their cutoff is too."""
    m = int(months)
    return [
        f"DELETE FROM user_activity_daily WHERE day < (now() AT TIME ZONE 'Asia/Tokyo')::date - INTERVAL '{m} months'",
        f"DELETE FROM login_events WHERE created_at < now() - INTERVAL '{m} months'",
        "DELETE FROM sessions WHERE expires_at < now()",
    ]


def policy_prune_sql() -> list[str]:
    """Every DELETE the privacy policy's stated periods depend on, in the order
    the daily pass runs them."""
    return [
        *personal_data_prune_sql(PERSONAL_DATA_RETENTION_MONTHS),
        prune_query_log_sql(QUERY_LOG_RETENTION_DAYS),
        prune_intent_cache_sql(QUERY_LOG_RETENTION_DAYS),
        prune_admin_audit_sql(ADMIN_AUDIT_RETENTION_DAYS),
    ]
