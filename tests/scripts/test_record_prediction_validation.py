"""scripts/record_prediction_validation.py writes a validation report's counts
to prediction_validations, only with --apply."""

import json

from scripts import record_prediction_validation as record
from scripts import validate_vp_predictions as vv


def _report(tmp_path, agency_id, *, kept_on_time=600, name="report.json"):
    summary = {
        "schema": vv.SCHEMA,
        "agency_id": agency_id,
        "from": "2026-11-13",
        "to": "2026-11-21",
        "thresholds": {"on_time_early_tolerance_sec": 60, "on_time_late_tolerance_sec": 300},
        "coverage": {"stop_events": 1000},
        "figures": {
            "on_time": {
                "n": 200 + kept_on_time,
                "kept_to_observed": {
                    "early": {"early": 100, "on_time": 0, "late": 0},
                    "on_time": {"early": 0, "on_time": kept_on_time, "late": 0},
                    "late": {"early": 0, "on_time": 0, "late": 100},
                },
            }
        },
        "days": [{"service_date": "2026-11-13", "timetable": "gtfs_static_20261101.zip", "missing_archives": []}],
    }
    path = tmp_path / name
    path.write_text(json.dumps(summary))
    return path


def _recorded(pg_conn, agency_id):
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT from_date::text, to_date::text, compared, kept_on_time, observed_on_time "
            "FROM prediction_validations WHERE agency_id = %s",
            (agency_id,),
        )
        return cur.fetchall()


def test_the_dry_run_prints_the_row_and_writes_nothing(pg_conn, agency_id, tmp_path, capsys):
    assert record.main([str(_report(tmp_path, agency_id))]) == 0
    out = capsys.readouterr().out
    assert f"agency {agency_id} 2026-11-13..2026-11-21: 800/1000 stop events compared" in out
    assert "; new" in out
    assert "dry run: 1 row(s) would be written; --apply writes them" in out
    assert _recorded(pg_conn, agency_id) == []


def test_apply_records_it_and_a_second_record_of_the_same_dates_replaces_it(pg_conn, agency_id, tmp_path, capsys):
    assert record.main([str(_report(tmp_path, agency_id)), "--apply"]) == 0
    assert "wrote 1 row(s)" in capsys.readouterr().out
    assert _recorded(pg_conn, agency_id) == [("2026-11-13", "2026-11-21", 800, 600, 600)]
    assert record.main([str(_report(tmp_path, agency_id, kept_on_time=500)), "--apply"]) == 0
    assert "replaces the recorded one" in capsys.readouterr().out
    assert _recorded(pg_conn, agency_id) == [("2026-11-13", "2026-11-21", 700, 500, 500)]


def test_a_listed_agency_and_a_soft_deleted_one_are_recorded_alike(pg_conn, agency_id, tmp_path):
    """A deleted agency's feed was still validated; the board leaving it out
    is no reason to lose what was measured."""
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    assert record.main([str(_report(tmp_path, agency_id)), "--apply"]) == 0
    assert len(_recorded(pg_conn, agency_id)) == 1


def test_an_agency_postgres_does_not_hold_is_refused_before_anything_is_written(pg_conn, agency_id, tmp_path, capsys):
    reports = [str(_report(tmp_path, agency_id)), str(_report(tmp_path, agency_id + 1000, name="other.json"))]
    assert record.main([*reports, "--apply"]) == 1
    assert f"refused: no agency {agency_id + 1000} in Postgres" in capsys.readouterr().err
    assert _recorded(pg_conn, agency_id) == []


def test_a_report_that_cannot_be_recorded_refuses_the_whole_run(pg_conn, agency_id, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": vv.SCHEMA - 1}))
    assert record.main([str(_report(tmp_path, agency_id)), str(bad), "--apply"]) == 1
    assert f"refused: {bad}: made under schema" in capsys.readouterr().err
    assert _recorded(pg_conn, agency_id) == []


def test_two_reports_for_the_same_agency_and_dates_refuse_the_run(pg_conn, agency_id, tmp_path, capsys):
    first = str(_report(tmp_path, agency_id))
    second = str(_report(tmp_path, agency_id, kept_on_time=500, name="rerun.json"))
    assert record.main([first, second, "--apply"]) == 1
    assert f"refused: {first} and {second} are both for agency {agency_id} 2026-11-13..2026-11-21" in (
        capsys.readouterr().err
    )
    assert _recorded(pg_conn, agency_id) == []
