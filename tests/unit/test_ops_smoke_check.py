"""Tests for scripts/ops_smoke_check.py: the post-install/post-rollback check that
all four operations-status collectors (vps_loop, github, oracle_crawler, r2) are
wired up correctly, distinct from whether the underlying systems they observe are
currently healthy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import ops_smoke_check
from scripts.ops_status import build_status, to_json_dict

T0 = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def make_component(component: str, *, state_kwargs: dict | None = None) -> dict:
    kwargs = dict(
        component=component,
        observed_at=T0,
        last_success_at=T0 - timedelta(minutes=5),
        healthy_max_age_seconds=3600,
        stale_max_age_seconds=7200,
        details={},
        now=T0,
    )
    kwargs.update(state_kwargs or {})
    return to_json_dict(build_status(**kwargs))


ALL_FOUR = [make_component(name) for name in ("vps_loop", "github", "oracle_crawler", "r2")]


# ── check_documents ─────────────────────────────────────────────────────────


def test_check_documents_passes_with_all_four_valid_components():
    assert ops_smoke_check.check_documents(ALL_FOUR) == []


def test_check_documents_reports_a_missing_component():
    documents = [doc for doc in ALL_FOUR if doc["component"] != "r2"]
    problems = ops_smoke_check.check_documents(documents)
    assert len(problems) == 1
    assert "r2" in problems[0]
    assert "missing" in problems[0]


def test_check_documents_reports_an_unexpected_component():
    # build_status itself rejects an unknown component name, so a malformed
    # collector output is simulated by mutating a valid document's field instead.
    bogus = dict(ALL_FOUR[0])
    bogus["component"] = "bogus"
    problems = ops_smoke_check.check_documents([*ALL_FOUR, bogus])
    assert any("unexpected" in problem and "bogus" in problem for problem in problems)


def test_check_documents_reports_a_schema_violation():
    broken = dict(ALL_FOUR[0])
    broken["state"] = "not_a_real_state"
    problems = ops_smoke_check.check_documents([broken, *ALL_FOUR[1:]])
    assert any("failed contract validation" in problem for problem in problems)


# ── check_collector_warnings ────────────────────────────────────────────────


def test_check_collector_warnings_empty_when_no_collector_errors():
    assert ops_smoke_check.check_collector_warnings(ALL_FOUR) == []


def test_check_collector_warnings_surfaces_a_collector_error_without_failing():
    with_error = make_component("github", state_kwargs={"details": {"collector_error": "gh: command not found"}})
    documents = [doc if doc["component"] != "github" else with_error for doc in ALL_FOUR]

    warnings = ops_smoke_check.check_collector_warnings(documents)
    problems = ops_smoke_check.check_documents(documents)

    assert len(warnings) == 1
    assert "collector_error" in warnings[0]
    assert "gh: command not found" in warnings[0]
    # A collector_error is a warning, not a contract violation -- the document
    # itself is still perfectly valid (an `unknown` state with a bounded detail).
    assert problems == []


# ── main (CLI wiring) ────────────────────────────────────────────────────────


def test_main_exits_zero_when_all_four_collect_and_validate(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(ops_smoke_check, "collect_all", lambda **kw: ALL_FOUR)

    exit_code = ops_smoke_check.main(["--repo", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "OK" in out


def test_main_exits_nonzero_when_a_component_is_missing(monkeypatch, tmp_path: Path, capsys):
    documents = [doc for doc in ALL_FOUR if doc["component"] != "oracle_crawler"]
    monkeypatch.setattr(ops_smoke_check, "collect_all", lambda **kw: documents)

    exit_code = ops_smoke_check.main(["--repo", str(tmp_path)])

    err = capsys.readouterr().err
    assert exit_code == 1
    assert "FAIL" in err
    assert "oracle_crawler" in err


def test_main_prints_warnings_but_still_exits_zero(monkeypatch, tmp_path: Path, capsys):
    with_error = make_component("r2", state_kwargs={"details": {"collector_error": "aws: no credentials"}})
    documents = [doc if doc["component"] != "r2" else with_error for doc in ALL_FOUR]
    monkeypatch.setattr(ops_smoke_check, "collect_all", lambda **kw: documents)

    exit_code = ops_smoke_check.main(["--repo", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WARN" in out
    assert "r2" in out
