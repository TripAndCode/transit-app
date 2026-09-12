"""Tests for scripts/collect_r2_status.py: the VPS-side other half of the
Oracle storage-metrics heartbeat channel (see
oracle_cloud/v3/bin/storage-metrics.sh and
.github/workflows/r2-storage-heartbeat-listener.yml for the Oracle-side and
GitHub-side legs).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.collect_r2_status import (
    R2StatusReplayed,
    R2StatusUnavailable,
    collect_r2_status,
    parse_latest_status,
)
from scripts.ops_status import build_status, to_json_dict

T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def make_document(observed_at: datetime, *, state_kwargs: dict | None = None) -> dict:
    kwargs = dict(
        component="r2",
        observed_at=observed_at,
        last_success_at=observed_at - timedelta(minutes=5),
        healthy_max_age_seconds=3600,
        stale_max_age_seconds=7200,
        details={"disk_used_pct": 42, "r2_configured": True},
    )
    kwargs.update(state_kwargs or {})
    return to_json_dict(build_status(**kwargs))


_LOG_PREFIX = "record\tRecord the R2 storage metrics status document\t2026-09-11T00:05:00.1234567Z "


def log_with(*docs: dict, prefixed: bool = True) -> str:
    """Build a fake `gh run view --log` body containing `R2_STORAGE_STATUS` lines.

    `prefixed=True` (the default) mimics real `gh run view --log` output,
    which prepends `<job>\\t<step>\\t<timestamp> ` to every line -- the
    marker is never at the true start of a line in production.
    """
    prefix = _LOG_PREFIX if prefixed else ""
    lines = ["some unrelated log line"]
    for doc in docs:
        lines.append(f"{prefix}R2_STORAGE_STATUS {json.dumps(doc, separators=(',', ':'))}")
    lines.append("Complete job name: record")
    return "\n".join(lines)


def fetcher_returning(text: str):
    return lambda repo: text


def fetcher_raising(exc: Exception):
    def _fetch(repo):
        raise exc

    return _fetch


# --- parse_latest_status -----------------------------------------------------


def test_parse_latest_status_picks_the_last_line_when_several_present():
    doc1 = make_document(T0)
    doc2 = make_document(T0 + timedelta(minutes=1))
    parsed = parse_latest_status(log_with(doc1, doc2))
    assert parsed["observed_at"] == doc2["observed_at"]


def test_parse_latest_status_finds_the_marker_behind_gh_run_view_log_prefix():
    doc = make_document(T0)
    parsed = parse_latest_status(log_with(doc, prefixed=True))
    assert parsed["observed_at"] == doc["observed_at"]


def test_parse_latest_status_also_accepts_an_unprefixed_bare_line():
    doc = make_document(T0)
    parsed = parse_latest_status(log_with(doc, prefixed=False))
    assert parsed["observed_at"] == doc["observed_at"]


def test_parse_latest_status_raises_when_no_line_present():
    with pytest.raises(R2StatusUnavailable, match="no R2_STORAGE_STATUS line"):
        parse_latest_status("nothing to see here\nComplete job name: record")


def test_parse_latest_status_raises_on_malformed_json():
    with pytest.raises(R2StatusUnavailable, match="not valid JSON"):
        parse_latest_status("R2_STORAGE_STATUS {not valid json}")


def test_parse_latest_status_raises_when_payload_is_not_an_object():
    with pytest.raises(R2StatusUnavailable, match="did not decode to a JSON object"):
        parse_latest_status("R2_STORAGE_STATUS [1, 2, 3]")


# --- collect_r2_status: happy path + freshness -------------------------------


def test_collect_r2_status_accepts_a_fresh_valid_document(tmp_path: Path):
    doc = make_document(T0)
    cache = tmp_path / "watermark.json"
    status = collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(doc)))
    assert status.component == "r2"
    assert status.observed_at == T0
    assert json.loads(cache.read_text())["observed_at"] == doc["observed_at"]


def test_collect_r2_status_accepts_a_strictly_newer_second_document(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    newer = make_document(T0 + timedelta(minutes=10))
    status = collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(newer)))
    assert status.observed_at == T0 + timedelta(minutes=10)


def test_collect_r2_status_rejects_a_replayed_identical_document(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    doc = make_document(T0)
    collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(doc)))
    watermark_before = cache.read_text()
    with pytest.raises(R2StatusReplayed) as excinfo:
        collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(doc)))
    assert excinfo.value.status.observed_at == T0
    # A rejected replay must never advance the watermark.
    assert cache.read_text() == watermark_before


def test_collect_r2_status_rejects_an_older_document_than_the_watermark(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    older = make_document(T0 - timedelta(minutes=30))
    with pytest.raises(R2StatusReplayed):
        collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(older)))


def test_collect_r2_status_with_no_prior_watermark_file_accepts_anything(tmp_path: Path):
    cache = tmp_path / "does-not-exist-yet.json"
    status = collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    assert status.observed_at == T0
    assert cache.exists()


# --- collect_r2_status: failure modes -----------------------------------------


def test_collect_r2_status_propagates_a_log_fetcher_failure(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    with pytest.raises(R2StatusUnavailable, match="gh run list"):
        collect_r2_status(
            cache_path=cache,
            log_fetcher=fetcher_raising(R2StatusUnavailable("gh run list failed")),
        )


def test_collect_r2_status_rejects_a_document_that_fails_contract_validation(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    bad_doc = make_document(T0)
    bad_doc["component"] = "not-a-real-component"
    with pytest.raises(R2StatusUnavailable, match="contract validation"):
        collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(bad_doc)))
    assert not cache.exists()


def test_collect_r2_status_rejects_a_contract_valid_document_for_another_component(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    other_component_doc = make_document(T0, state_kwargs={"component": "vps_loop"})
    with pytest.raises(R2StatusUnavailable, match="expected 'r2'"):
        collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(other_component_doc)))
    assert not cache.exists()


def test_collect_r2_status_leaves_no_watermark_after_an_unavailable_channel(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    with pytest.raises(R2StatusUnavailable):
        collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning("no heartbeat lines here"))
    assert not cache.exists()


def test_collect_r2_status_tolerates_a_corrupt_watermark_cache(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    cache.write_text("not json at all", encoding="utf-8")
    status = collect_r2_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    assert status.observed_at == T0


# --- default_log_fetcher: gh invocation failures -----------------------------


def test_default_log_fetcher_reports_unavailable_when_gh_list_exits_nonzero(monkeypatch):
    from scripts import collect_r2_status as module

    class FakeCompleted:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: FakeCompleted(1, "", "boom"))
    with pytest.raises(R2StatusUnavailable, match="gh run list"):
        module.default_log_fetcher("TripAndCode/transit-app")


def test_default_log_fetcher_reports_unavailable_when_no_run_exists(monkeypatch):
    from scripts import collect_r2_status as module

    class FakeCompleted:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    # `gh run list -q '.[0].databaseId'` resolves to the literal string
    # "null" when the result list is empty, matching vps-heartbeat-
    # watchdog.yml's own documented handling of this exact `gh`/`jq` quirk.
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: FakeCompleted(0, "null\n", ""))
    with pytest.raises(R2StatusUnavailable, match=r"no r2-storage-heartbeat-listener\.yml run"):
        module.default_log_fetcher("TripAndCode/transit-app")


def test_default_log_fetcher_reports_unavailable_when_gh_is_missing(monkeypatch):
    from scripts import collect_r2_status as module

    def _raise(*a, **k):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(module.subprocess, "run", _raise)
    with pytest.raises(R2StatusUnavailable, match="could not be executed"):
        module.default_log_fetcher("TripAndCode/transit-app")


# --- CLI -----------------------------------------------------------------------


def test_main_exit_code_0_and_prints_the_status_on_a_fresh_document(tmp_path: Path, monkeypatch, capsys):
    from scripts import collect_r2_status as module

    doc = make_document(T0)
    monkeypatch.setattr(
        module,
        "collect_r2_status",
        lambda **kwargs: collect_r2_status(**{**kwargs, "log_fetcher": fetcher_returning(log_with(doc))}),
    )

    exit_code = module.main(["--cache-path", str(tmp_path / "watermark.json")])

    payload = capsys.readouterr().out
    assert exit_code == 0
    assert '"component": "r2"' in payload


def test_main_exit_code_1_and_reports_a_replayed_document(tmp_path: Path, monkeypatch, capsys):
    from scripts import collect_r2_status as module

    doc = make_document(T0)

    def _raise(**kwargs):
        seen = collect_r2_status(**{**kwargs, "log_fetcher": fetcher_returning(log_with(doc))})
        raise R2StatusReplayed("already seen", seen)

    monkeypatch.setattr(module, "collect_r2_status", _raise)

    exit_code = module.main(["--cache-path", str(tmp_path / "watermark.json")])

    assert exit_code == 1
    assert "REPLAYED:" in capsys.readouterr().err


def test_main_exit_code_2_and_reports_an_unavailable_channel(tmp_path: Path, monkeypatch, capsys):
    from scripts import collect_r2_status as module

    def _raise(**kwargs):
        raise R2StatusUnavailable("no gh")

    monkeypatch.setattr(module, "collect_r2_status", _raise)

    exit_code = module.main(["--cache-path", str(tmp_path / "watermark.json")])

    assert exit_code == 2
    assert "UNAVAILABLE:" in capsys.readouterr().err
