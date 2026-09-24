"""Pure SQL text for the `prune-pipeline-runs` and `prune-admin-audit` CLI
commands (`gtfs_pipeline.py`), next to the existing `prune_query_log`.

DB-free: these only build the DELETE text; `cmd_prune_pipeline_runs`/
`cmd_prune_admin_audit` (not exercised here) open the real connection.
"""

import gtfs_pipeline


def test_pipeline_runs_retention_default_is_90_days():
    assert gtfs_pipeline.PIPELINE_RUNS_RETENTION_DAYS == 90


def test_admin_audit_retention_default_matches_the_deploys_400_day_horizon():
    assert gtfs_pipeline.ADMIN_AUDIT_RETENTION_DAYS == 400


def test_prune_pipeline_runs_sql_targets_started_at():
    sql = gtfs_pipeline.prune_pipeline_runs_sql(90)
    assert "DELETE FROM pipeline_runs" in sql
    assert "started_at < now() - INTERVAL '90 days'" in sql


def test_prune_admin_audit_sql_targets_at():
    sql = gtfs_pipeline.prune_admin_audit_sql(400)
    assert "DELETE FROM admin_audit" in sql
    assert "at < now() - INTERVAL '400 days'" in sql


def test_prune_pipeline_runs_subcommand_dispatches(monkeypatch):
    called = {}
    monkeypatch.setattr(gtfs_pipeline, "cmd_prune_pipeline_runs", lambda args: called.setdefault("days", args.days))
    monkeypatch.setattr("sys.argv", ["gtfs_pipeline.py", "prune-pipeline-runs"])
    gtfs_pipeline.main()
    assert called["days"] == gtfs_pipeline.PIPELINE_RUNS_RETENTION_DAYS


def test_prune_admin_audit_subcommand_dispatches(monkeypatch):
    called = {}
    monkeypatch.setattr(gtfs_pipeline, "cmd_prune_admin_audit", lambda args: called.setdefault("days", args.days))
    monkeypatch.setattr("sys.argv", ["gtfs_pipeline.py", "prune-admin-audit", "--days", "30"])
    gtfs_pipeline.main()
    assert called["days"] == 30
