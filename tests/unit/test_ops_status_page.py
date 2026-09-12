"""Tests for scripts/ops_status_page.py: the item-123 aggregator that combines the four
independent operations-status collectors (items 119-122) into one combined document, and
renders it as compact text / HTML / (via `build_document`'s own dict) JSON.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import ops_status_page
from scripts.collect_oracle_status import OracleStatusReplayed, OracleStatusUnavailable
from scripts.collect_r2_status import R2StatusReplayed, R2StatusUnavailable
from scripts.collect_vps_status import VpsStatusUnavailable
from scripts.ops_status import build_status, to_json_dict

T0 = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


def make_component(
    component: str,
    *,
    state_kwargs: dict | None = None,
) -> dict:
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


# ── collect_all: per-collector isolation ──────────────────────────────────


def test_collect_all_returns_all_four_components_on_success(monkeypatch):
    monkeypatch.setattr(
        ops_status_page,
        "collect_vps_loop_status",
        lambda **kw: build_status(**_status_kwargs("vps_loop")),
    )
    monkeypatch.setattr(
        ops_status_page,
        "collect_github_status",
        lambda **kw: build_status(**_status_kwargs("github")),
    )
    monkeypatch.setattr(
        ops_status_page,
        "collect_oracle_status",
        lambda **kw: build_status(**_status_kwargs("oracle_crawler")),
    )
    monkeypatch.setattr(
        ops_status_page,
        "collect_r2_status",
        lambda **kw: build_status(**_status_kwargs("r2")),
    )

    documents = ops_status_page.collect_all(now=T0)

    assert [d["component"] for d in documents] == ["vps_loop", "github", "oracle_crawler", "r2"]
    assert all(d["state"] == "healthy" for d in documents)


def _status_kwargs(component: str) -> dict:
    return dict(
        component=component,
        observed_at=T0,
        last_success_at=T0 - timedelta(minutes=1),
        healthy_max_age_seconds=3600,
        stale_max_age_seconds=7200,
        details={},
        now=T0,
    )


def test_collect_all_isolates_one_collectors_failure(monkeypatch):
    def boom(**kw):
        raise RuntimeError("systemctl not found")

    monkeypatch.setattr(ops_status_page, "collect_vps_loop_status", boom)
    monkeypatch.setattr(ops_status_page, "collect_github_status", lambda **kw: build_status(**_status_kwargs("github")))
    monkeypatch.setattr(
        ops_status_page, "collect_oracle_status", lambda **kw: build_status(**_status_kwargs("oracle_crawler"))
    )
    monkeypatch.setattr(ops_status_page, "collect_r2_status", lambda **kw: build_status(**_status_kwargs("r2")))

    documents = ops_status_page.collect_all(now=T0)
    by_component = {d["component"]: d for d in documents}

    assert by_component["vps_loop"]["state"] == "unknown"
    assert "systemctl not found" in by_component["vps_loop"]["details"]["collector_error"]
    assert by_component["github"]["state"] == "healthy"
    assert by_component["oracle_crawler"]["state"] == "healthy"
    assert by_component["r2"]["state"] == "healthy"


def test_collect_all_vps_loop_unavailable_degrades_to_unknown(monkeypatch):
    def boom(**kw):
        raise VpsStatusUnavailable("NEXT_TASK.md does not exist")

    monkeypatch.setattr(ops_status_page, "collect_vps_loop_status", boom)
    monkeypatch.setattr(ops_status_page, "collect_github_status", lambda **kw: build_status(**_status_kwargs("github")))
    monkeypatch.setattr(
        ops_status_page, "collect_oracle_status", lambda **kw: build_status(**_status_kwargs("oracle_crawler"))
    )
    monkeypatch.setattr(ops_status_page, "collect_r2_status", lambda **kw: build_status(**_status_kwargs("r2")))

    documents = ops_status_page.collect_all(now=T0)
    by_component = {d["component"]: d for d in documents}
    assert by_component["vps_loop"]["state"] == "unknown"
    assert "NEXT_TASK.md" in by_component["vps_loop"]["details"]["collector_error"]


def test_collect_all_oracle_replayed_uses_carried_status_not_unknown(monkeypatch):
    replayed_status = build_status(**_status_kwargs("oracle_crawler"))

    def replayed(**kw):
        raise OracleStatusReplayed("not newer than watermark", replayed_status)

    monkeypatch.setattr(
        ops_status_page, "collect_vps_loop_status", lambda **kw: build_status(**_status_kwargs("vps_loop"))
    )
    monkeypatch.setattr(ops_status_page, "collect_github_status", lambda **kw: build_status(**_status_kwargs("github")))
    monkeypatch.setattr(ops_status_page, "collect_oracle_status", replayed)
    monkeypatch.setattr(ops_status_page, "collect_r2_status", lambda **kw: build_status(**_status_kwargs("r2")))

    documents = ops_status_page.collect_all(now=T0)
    by_component = {d["component"]: d for d in documents}
    assert by_component["oracle_crawler"]["state"] == "healthy"
    assert "collector_error" not in by_component["oracle_crawler"]["details"]


def test_collect_all_oracle_unavailable_degrades_to_unknown(monkeypatch):
    def boom(**kw):
        raise OracleStatusUnavailable("no gh run found")

    monkeypatch.setattr(
        ops_status_page, "collect_vps_loop_status", lambda **kw: build_status(**_status_kwargs("vps_loop"))
    )
    monkeypatch.setattr(ops_status_page, "collect_github_status", lambda **kw: build_status(**_status_kwargs("github")))
    monkeypatch.setattr(ops_status_page, "collect_oracle_status", boom)
    monkeypatch.setattr(ops_status_page, "collect_r2_status", lambda **kw: build_status(**_status_kwargs("r2")))

    documents = ops_status_page.collect_all(now=T0)
    by_component = {d["component"]: d for d in documents}
    assert by_component["oracle_crawler"]["state"] == "unknown"
    assert "no gh run found" in by_component["oracle_crawler"]["details"]["collector_error"]


def test_collect_all_r2_replayed_uses_carried_status(monkeypatch):
    replayed_status = build_status(**_status_kwargs("r2"))

    def replayed(**kw):
        raise R2StatusReplayed("not newer than watermark", replayed_status)

    monkeypatch.setattr(
        ops_status_page, "collect_vps_loop_status", lambda **kw: build_status(**_status_kwargs("vps_loop"))
    )
    monkeypatch.setattr(ops_status_page, "collect_github_status", lambda **kw: build_status(**_status_kwargs("github")))
    monkeypatch.setattr(
        ops_status_page, "collect_oracle_status", lambda **kw: build_status(**_status_kwargs("oracle_crawler"))
    )
    monkeypatch.setattr(ops_status_page, "collect_r2_status", replayed)

    documents = ops_status_page.collect_all(now=T0)
    by_component = {d["component"]: d for d in documents}
    assert by_component["r2"]["state"] == "healthy"
    assert "collector_error" not in by_component["r2"]["details"]


def test_collect_all_r2_unavailable_degrades_to_unknown(monkeypatch):
    def boom(**kw):
        raise R2StatusUnavailable("no gh run found")

    monkeypatch.setattr(
        ops_status_page, "collect_vps_loop_status", lambda **kw: build_status(**_status_kwargs("vps_loop"))
    )
    monkeypatch.setattr(ops_status_page, "collect_github_status", lambda **kw: build_status(**_status_kwargs("github")))
    monkeypatch.setattr(
        ops_status_page, "collect_oracle_status", lambda **kw: build_status(**_status_kwargs("oracle_crawler"))
    )
    monkeypatch.setattr(ops_status_page, "collect_r2_status", boom)

    documents = ops_status_page.collect_all(now=T0)
    by_component = {d["component"]: d for d in documents}
    assert by_component["r2"]["state"] == "unknown"


def test_unknown_status_reason_is_bounded_and_single_line():
    reason = "boom: " + ("x" * 500) + "\nsome traceback line"
    doc = ops_status_page._unknown_status("r2", now=T0, reason=reason)
    assert doc["state"] == "unknown"
    assert "\n" not in doc["details"]["collector_error"]
    assert len(doc["details"]["collector_error"]) <= 200


def test_unknown_status_scrubs_embedded_github_tokens():
    reason = "HTTPError: authorization failed for token ghp_1234567890abcdefabcdefabcdefabcdef"
    doc = ops_status_page._unknown_status("github", now=T0, reason=reason)
    assert "ghp_1234567890abcdefabcdefabcdefabcdef" not in doc["details"]["collector_error"]
    assert "[REDACTED]" in doc["details"]["collector_error"]


# ── overall_state ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "states,expected",
    [
        (["healthy", "healthy", "healthy", "healthy"], "healthy"),
        (["healthy", "degraded", "healthy", "healthy"], "degraded"),
        (["healthy", "unknown", "healthy", "healthy"], "unknown"),
        (["healthy", "degraded", "unknown", "healthy"], "degraded"),
        (["healthy", "unknown", "degraded", "healthy"], "degraded"),
        (["unknown", "stale", "healthy", "healthy"], "stale"),
        (["stale", "failed", "healthy", "healthy"], "failed"),
        ([], "unknown"),
    ],
)
def test_overall_state_takes_the_worst(states, expected):
    # Minimal dicts, not full ComponentStatus documents: overall_state only reads
    # `state`, and build_status enforces cross-field invariants irrelevant here.
    documents = [{"state": s} for s in states]
    assert ops_status_page.overall_state(documents) == expected


# ── build_document ──────────────────────────────────────────────────────────


def test_build_document_shape_and_ordering():
    docs = [
        make_component("r2"),
        make_component("vps_loop"),
        make_component("oracle_crawler"),
        make_component("github"),
    ]
    document = ops_status_page.build_document(docs, now=T0)

    assert document["schema_version"] == ops_status_page.DOCUMENT_SCHEMA_VERSION
    assert document["generated_at"] == "2026-09-12T12:00:00Z"
    assert document["overall_state"] == "healthy"
    assert [c["component"] for c in document["components"]] == ["vps_loop", "github", "oracle_crawler", "r2"]
    assert document["reasons"] == {}


def test_build_document_populates_reasons_for_non_healthy_only():
    healthy = make_component("vps_loop")
    failed = make_component("github", state_kwargs={"reported_failure": True})
    docs = [healthy, failed]
    document = ops_status_page.build_document(docs, now=T0)

    assert "vps_loop" not in document["reasons"]
    assert "github" in document["reasons"]
    assert document["overall_state"] == "failed"


# ── reason_for ──────────────────────────────────────────────────────────────


def test_reason_for_healthy_is_none():
    doc = make_component("vps_loop")
    assert ops_status_page.reason_for(doc) is None


def test_reason_for_collector_error_takes_priority():
    doc = ops_status_page._unknown_status("github", now=T0, reason="RuntimeError: gh not found")
    reason = ops_status_page.reason_for(doc)
    assert reason is not None
    assert "collector could not run" in reason
    assert "gh not found" in reason


def test_reason_for_vps_loop_restarting():
    doc = make_component(
        "vps_loop",
        state_kwargs={"reported_failure": True, "details": {"loop_activity": "restarting", "blocker_class": "db-lock"}},
    )
    assert ops_status_page.reason_for(doc) == "repeatedly blocked on db-lock without making progress"


def test_reason_for_vps_loop_paused():
    doc = make_component(
        "vps_loop",
        state_kwargs={"reported_failure": True, "details": {"loop_activity": "paused", "blocker_class": "review-gate"}},
    )
    assert ops_status_page.reason_for(doc) == "circuit-breaker paused (blocked on review-gate)"


def test_reason_for_vps_loop_systemd_failed_falls_back_when_no_activity_reason():
    doc = make_component(
        "vps_loop",
        state_kwargs={"reported_failure": True, "details": {"loop_activity": "idle", "systemd_active_state": "failed"}},
    )
    assert ops_status_page.reason_for(doc) == "claude-loop.service reported a failed run"


def test_reason_for_github_summarizes_prs():
    doc = make_component(
        "github",
        state_kwargs={
            "reported_failure": True,
            "details": {"conflicting_pr_count": 2, "failing_checks_pr_count": 1},
        },
    )
    reason = ops_status_page.reason_for(doc)
    assert "2 PR(s) conflicting" in reason
    assert "1 PR(s) with failing checks" in reason


def test_reason_for_github_last_error_kind():
    doc = make_component(
        "github",
        state_kwargs={"last_success_at": None, "details": {"last_error_kind": "rate_limited"}},
    )
    reason = ops_status_page.reason_for(doc)
    assert "rate_limited" in reason


def test_reason_for_oracle_crawler_names_the_bad_feed():
    doc = make_component(
        "oracle_crawler",
        state_kwargs={
            "reported_failure": True,
            "details": {"rt_state": "healthy", "static_state": "stale", "r2_state": "healthy"},
        },
    )
    reason = ops_status_page.reason_for(doc)
    assert reason == "static feed is stale"


def test_reason_for_oracle_crawler_not_applicable_is_not_a_reason():
    doc = make_component(
        "oracle_crawler",
        state_kwargs={
            "reported_failure": True,
            "details": {"rt_state": "healthy", "static_state": "not_applicable", "r2_state": "healthy"},
        },
    )
    # Nothing is actually wrong per the mined details; falls back to the generic reason.
    reason = ops_status_page.reason_for(doc)
    assert reason == "the component itself reported an explicit failure"


def test_reason_for_r2_names_disk_and_listing_state():
    doc = make_component(
        "r2",
        state_kwargs={
            "reported_failure": True,
            "details": {
                "disk_state": "degraded",
                "disk_used_pct": 91,
                "r2_state": "failed",
                "r2_listing_result": "timeout",
            },
        },
    )
    reason = ops_status_page.reason_for(doc)
    assert "disk usage is 91% (degraded)" in reason
    assert "R2 listing is failed (timeout)" in reason


def test_reason_for_generic_stale_mentions_age():
    doc = make_component(
        "r2",
        state_kwargs={
            "observed_at": T0,
            "last_success_at": T0 - timedelta(hours=3),
            "healthy_max_age_seconds": 60,
            "stale_max_age_seconds": 3600,
        },
    )
    reason = ops_status_page.reason_for(doc)
    assert "past the staleness threshold" in reason


def test_reason_for_generic_unknown_never_succeeded():
    doc = make_component("r2", state_kwargs={"last_success_at": None})
    assert ops_status_page.reason_for(doc) == "no successful observation has ever been recorded"


# ── highlight_for ────────────────────────────────────────────────────────────


def test_highlight_for_vps_loop():
    doc = make_component("vps_loop", state_kwargs={"details": {"current_item": 123, "loop_activity": "idle"}})
    assert ops_status_page.highlight_for(doc) == "current_item=123 activity=idle"


def test_highlight_for_unrecognized_component_is_empty():
    doc = make_component("vps_loop")
    doc["component"] = "not_a_real_component"
    assert ops_status_page.highlight_for(doc) == ""


def test_highlight_for_never_raises_on_malformed_details():
    doc = make_component("github")
    doc["details"] = "not-a-dict"
    assert ops_status_page.highlight_for(doc) == ""


# ── render_text / render_html ────────────────────────────────────────────────


def test_render_text_includes_overall_and_each_component():
    docs = [make_component(c) for c in ops_status_page.COMPONENT_ORDER]
    document = ops_status_page.build_document(docs, now=T0)
    text = ops_status_page.render_text(document)
    assert "overall=healthy" in text
    for component in ops_status_page.COMPONENT_ORDER:
        assert component in text


def test_render_text_shows_reason_for_non_healthy():
    healthy_docs = [make_component(c) for c in ops_status_page.COMPONENT_ORDER if c != "github"]
    failed_github = make_component(
        "github", state_kwargs={"reported_failure": True, "details": {"conflicting_pr_count": 1}}
    )
    document = ops_status_page.build_document([*healthy_docs, failed_github], now=T0)
    text = ops_status_page.render_text(document)
    assert "reason:" in text
    assert "conflicting" in text


def test_render_html_escapes_hostile_detail_values():
    doc = make_component(
        "github",
        state_kwargs={"reported_failure": True, "details": {"last_error_kind": "<script>alert(1)</script>"}},
    )
    document = ops_status_page.build_document([doc], now=T0)
    page = ops_status_page.render_html(document)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_render_html_is_well_formed_table():
    docs = [make_component(c) for c in ops_status_page.COMPONENT_ORDER]
    document = ops_status_page.build_document(docs, now=T0)
    page = ops_status_page.render_html(document)
    assert page.startswith("<!doctype html>")
    assert page.count("<tr>") == len(docs) + 1  # one row per component, plus the header row
    assert 'class="state-healthy"' in page


# ── CLI main() ────────────────────────────────────────────────────────────


def test_main_text_format_exit_code_healthy(monkeypatch, capsys, tmp_path):
    docs = [make_component(c) for c in ops_status_page.COMPONENT_ORDER]
    monkeypatch.setattr(ops_status_page, "collect_all", lambda **kw: docs)

    exit_code = ops_status_page.main(["--repo", str(tmp_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "overall=healthy" in out


def test_main_json_format(monkeypatch, capsys, tmp_path):
    docs = [make_component(c) for c in ops_status_page.COMPONENT_ORDER]
    monkeypatch.setattr(ops_status_page, "collect_all", lambda **kw: docs)

    exit_code = ops_status_page.main(["--repo", str(tmp_path), "--format", "json"])

    assert exit_code == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["overall_state"] == "healthy"


def test_main_exit_code_nonzero_when_overall_failed(monkeypatch, capsys, tmp_path):
    docs = [make_component(c) for c in ops_status_page.COMPONENT_ORDER]
    docs[0] = make_component(ops_status_page.COMPONENT_ORDER[0], state_kwargs={"reported_failure": True})
    monkeypatch.setattr(ops_status_page, "collect_all", lambda **kw: docs)

    exit_code = ops_status_page.main(["--repo", str(tmp_path)])

    assert exit_code == 1


def test_main_writes_out_file(monkeypatch, tmp_path, capsys):
    docs = [make_component(c) for c in ops_status_page.COMPONENT_ORDER]
    monkeypatch.setattr(ops_status_page, "collect_all", lambda **kw: docs)
    out_path = tmp_path / "combined.json"

    exit_code = ops_status_page.main(["--repo", str(tmp_path), "--out", str(out_path)])

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["overall_state"] == "healthy"
