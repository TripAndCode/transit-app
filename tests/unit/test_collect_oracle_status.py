"""Tests for scripts/collect_oracle_status.py: the VPS-side other half of the
Oracle collector heartbeat channel (see oracle_cloud/v3/bin/publish-status.sh
and .github/workflows/oracle-heartbeat-listener.yml for the Oracle-side and
GitHub-side legs).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.collect_oracle_status import (
    OracleStatusReplayed,
    OracleStatusUnavailable,
    collect_oracle_status,
    parse_latest_status,
)
from scripts.ops_status import build_status, to_json_dict

T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def make_document(observed_at: datetime, *, state_kwargs: dict | None = None) -> dict:
    kwargs = dict(
        component="oracle_crawler",
        observed_at=observed_at,
        last_success_at=observed_at - timedelta(minutes=5),
        healthy_max_age_seconds=3600,
        stale_max_age_seconds=7200,
        details={"agencies_configured": 2},
    )
    kwargs.update(state_kwargs or {})
    return to_json_dict(build_status(**kwargs))


_LOG_PREFIX = "record\tRecord the Oracle collector's status document\t2026-09-11T00:05:00.1234567Z "


def log_with(*docs: dict, prefixed: bool = True) -> str:
    """Build a fake `gh run view --log` body containing `ORACLE_STATUS` lines.

    `prefixed=True` (the default) mimics real `gh run view --log` output,
    which prepends `<job>\\t<step>\\t<timestamp> ` to every line -- the
    marker is never at the true start of a line in production.
    """
    prefix = _LOG_PREFIX if prefixed else ""
    lines = ["some unrelated log line"]
    for doc in docs:
        lines.append(f"{prefix}ORACLE_STATUS {json.dumps(doc, separators=(',', ':'))}")
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
    # Real `gh run view --log` output prefixes every line with
    # `<job>\t<step>\t<timestamp> `, so ORACLE_STATUS is never at the true
    # start of a line in production -- only a substring match can find it.
    doc = make_document(T0)
    parsed = parse_latest_status(log_with(doc, prefixed=True))
    assert parsed["observed_at"] == doc["observed_at"]


def test_parse_latest_status_also_accepts_an_unprefixed_bare_line():
    doc = make_document(T0)
    parsed = parse_latest_status(log_with(doc, prefixed=False))
    assert parsed["observed_at"] == doc["observed_at"]


def test_parse_latest_status_raises_when_no_line_present():
    with pytest.raises(OracleStatusUnavailable, match="no ORACLE_STATUS line"):
        parse_latest_status("nothing to see here\nComplete job name: record")


def test_parse_latest_status_raises_on_malformed_json():
    with pytest.raises(OracleStatusUnavailable, match="not valid JSON"):
        parse_latest_status("ORACLE_STATUS {not valid json}")


def test_parse_latest_status_raises_when_payload_is_not_an_object():
    with pytest.raises(OracleStatusUnavailable, match="did not decode to a JSON object"):
        parse_latest_status("ORACLE_STATUS [1, 2, 3]")


# --- collect_oracle_status: happy path + freshness ---------------------------


def test_collect_oracle_status_accepts_a_fresh_valid_document(tmp_path: Path):
    doc = make_document(T0)
    cache = tmp_path / "watermark.json"
    status = collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(doc)))
    assert status.component == "oracle_crawler"
    assert status.observed_at == T0
    assert json.loads(cache.read_text())["observed_at"] == doc["observed_at"]


def test_collect_oracle_status_accepts_a_strictly_newer_second_document(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    newer = make_document(T0 + timedelta(minutes=10))
    status = collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(newer)))
    assert status.observed_at == T0 + timedelta(minutes=10)


def test_collect_oracle_status_rejects_a_replayed_identical_document(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    doc = make_document(T0)
    collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(doc)))
    watermark_before = cache.read_text()
    with pytest.raises(OracleStatusReplayed) as excinfo:
        collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(doc)))
    assert excinfo.value.status.observed_at == T0
    # A rejected replay must never advance the watermark.
    assert cache.read_text() == watermark_before


def test_collect_oracle_status_rejects_an_older_document_than_the_watermark(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    older = make_document(T0 - timedelta(minutes=30))
    with pytest.raises(OracleStatusReplayed):
        collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(older)))


def test_collect_oracle_status_with_no_prior_watermark_file_accepts_anything(tmp_path: Path):
    cache = tmp_path / "does-not-exist-yet.json"
    status = collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    assert status.observed_at == T0
    assert cache.exists()


# --- collect_oracle_status: failure modes ------------------------------------


def test_collect_oracle_status_propagates_a_log_fetcher_failure(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    with pytest.raises(OracleStatusUnavailable, match="gh run list"):
        collect_oracle_status(
            cache_path=cache,
            log_fetcher=fetcher_raising(OracleStatusUnavailable("gh run list failed")),
        )


def test_collect_oracle_status_rejects_a_document_that_fails_contract_validation(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    bad_doc = make_document(T0)
    bad_doc["component"] = "not-a-real-component"
    with pytest.raises(OracleStatusUnavailable, match="contract validation"):
        collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(bad_doc)))
    assert not cache.exists()


def test_collect_oracle_status_rejects_a_contract_valid_document_for_another_component(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    other_component_doc = make_document(T0, state_kwargs={"component": "vps_loop"})
    with pytest.raises(OracleStatusUnavailable, match="expected 'oracle_crawler'"):
        collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(other_component_doc)))
    assert not cache.exists()


def test_collect_oracle_status_leaves_no_watermark_after_an_unavailable_channel(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    with pytest.raises(OracleStatusUnavailable):
        collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning("no heartbeat lines here"))
    assert not cache.exists()


def test_collect_oracle_status_tolerates_a_corrupt_watermark_cache(tmp_path: Path):
    cache = tmp_path / "watermark.json"
    cache.write_text("not json at all", encoding="utf-8")
    status = collect_oracle_status(cache_path=cache, log_fetcher=fetcher_returning(log_with(make_document(T0))))
    assert status.observed_at == T0


# --- default_log_fetcher: gh invocation failures -----------------------------


def test_default_log_fetcher_reports_unavailable_when_gh_list_exits_nonzero(monkeypatch):
    from scripts import collect_oracle_status as module

    class FakeCompleted:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: FakeCompleted(1, "", "boom"))
    with pytest.raises(OracleStatusUnavailable, match="gh run list"):
        module.default_log_fetcher("TripAndCode/transit-app")


def test_default_log_fetcher_reports_unavailable_when_no_run_exists(monkeypatch):
    from scripts import collect_oracle_status as module

    class FakeCompleted:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    # `gh run list -q '.[0].databaseId'` resolves to the literal string
    # "null" when the result list is empty, matching vps-heartbeat-
    # watchdog.yml's own documented handling of this exact `gh`/`jq` quirk.
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: FakeCompleted(0, "null\n", ""))
    with pytest.raises(OracleStatusUnavailable, match=r"no oracle-heartbeat-listener\.yml run"):
        module.default_log_fetcher("TripAndCode/transit-app")


def test_default_log_fetcher_reports_unavailable_when_gh_is_missing(monkeypatch):
    from scripts import collect_oracle_status as module

    def _raise(*a, **k):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(module.subprocess, "run", _raise)
    with pytest.raises(OracleStatusUnavailable, match="could not be executed"):
        module.default_log_fetcher("TripAndCode/transit-app")
