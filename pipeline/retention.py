"""Retention windows and the DELETE text that enforces them.

The operational logs (`ask_query_log`, `pipeline_runs`, `admin_audit`) are
pruned by their `gtfs_pipeline.py` commands. The personal data the privacy
policy states a period for -- per-user usage counts and login history, kept
for ``PERSONAL_DATA_RETENTION_MONTHS``, and session rows past their expiry --
is also pruned daily by the API process (``api/retention.py``), so the stated
periods hold without an external scheduler.

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
