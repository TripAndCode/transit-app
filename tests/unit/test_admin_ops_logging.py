"""Unit tests for admin_ops' swallowed-failure logging (finding D1).

admin_ops degrades gracefully when a sub-check fails (see
pipeline.health.aggregate_freshness's identical rationale), but a silent
``except Exception: pass`` gave operators no trail to diagnose why a section
went null. These call the route function directly with mocked deps (no DB)
and assert the failure is logged with a traceback.
"""

import logging

from api.routers.admin import admin_ops
from pipeline.health import MigrationStatus


async def test_admin_ops_logs_migration_status_failure(caplog, monkeypatch):
    from pipeline import health as health_mod

    async def boom(conn):
        raise RuntimeError("simulated migration_status failure")

    async def ok_freshness(conn, ch):
        return []

    monkeypatch.setattr(health_mod, "migration_status", boom)
    monkeypatch.setattr(health_mod, "aggregate_freshness", ok_freshness)

    with caplog.at_level(logging.WARNING, logger="api.routers.admin"):
        result = await admin_ops(_admin=None, conn=None, ch=None)

    assert result.migrations is None
    records = [r for r in caplog.records if r.name == "api.routers.admin"]
    assert records, "expected a warning log from admin_ops on migration_status failure"
    assert any(r.exc_info for r in records), "expected exc_info=True so the traceback is captured"


async def test_admin_ops_logs_aggregate_freshness_failure(caplog, monkeypatch):
    from pipeline import health as health_mod

    async def ok_migration(conn):
        return MigrationStatus(applied="0026", latest="0026", behind=0)

    async def boom(conn, ch):
        raise RuntimeError("simulated aggregate_freshness failure")

    monkeypatch.setattr(health_mod, "migration_status", ok_migration)
    monkeypatch.setattr(health_mod, "aggregate_freshness", boom)

    with caplog.at_level(logging.WARNING, logger="api.routers.admin"):
        result = await admin_ops(_admin=None, conn=None, ch=None)

    assert result.agencies == []
    assert result.agencies_ok is False
    records = [r for r in caplog.records if r.name == "api.routers.admin"]
    assert records, "expected a warning log from admin_ops on aggregate_freshness failure"
    assert any(r.exc_info for r in records), "expected exc_info=True so the traceback is captured"
