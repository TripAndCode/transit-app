"""Selection, scheduling and orchestration of the unattended coverage re-probe.

No DB and no network: the registry rows, the clock, the feed fetch and the
recording step are all handed in or stubbed. The DB path (what actually lands
in `rt_field_coverage_probes` and `pipeline_runs`) is covered by
`tests/pipeline/test_rt_coverage_reprobe.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from pipeline import rt_coverage_reprobe as reprobe
from pipeline import runs as pipeline_runs
from pipeline.rt_coverage_reprobe import (
    MIN_SAMPLE,
    RENEW_WITHIN,
    Candidate,
    RecordedField,
    group_candidates,
    in_service_window,
    needs_reprobe,
    select_due,
)
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

JST = timezone(timedelta(hours=9))
# 11:00 JST: inside every agency's window, and far from both of its edges.
NOON_ISH = datetime(2026, 10, 27, 11, 0, tzinfo=JST)


def _fields(*, confirmed: bool = True, expires_at: datetime | None = None, only=RT_COVERAGE_FIELDS):
    return tuple(RecordedField(name, confirmed, expires_at) for name in only)


def _candidate(agency_id: int = 8, *, fields=None, tz: str = "Asia/Tokyo") -> Candidate:
    return Candidate(
        agency_id=agency_id,
        feed_url=f"https://feeds.example.jp/realtime/{agency_id}/trip_updates.bin",
        timezone=tz,
        fields=_fields(expires_at=NOON_ISH + timedelta(days=90)) if fields is None else fields,
    )


# ── which verdicts are due ─────────────────────────────────────────────────


def test_a_complete_live_confirmed_verdict_is_left_alone():
    assert not needs_reprobe(_fields(expires_at=NOON_ISH + RENEW_WITHIN + timedelta(hours=1)), NOON_ISH)


def test_an_agency_never_probed_is_due():
    assert needs_reprobe((), NOON_ISH)


def test_a_partial_verdict_is_due():
    assert needs_reprobe(_fields(expires_at=NOON_ISH + timedelta(days=90), only=RT_COVERAGE_FIELDS[:3]), NOON_ISH)


def test_an_expired_verdict_is_due():
    assert needs_reprobe(_fields(expires_at=NOON_ISH - timedelta(days=16)), NOON_ISH)


def test_a_verdict_inside_the_renewal_margin_is_due():
    assert needs_reprobe(_fields(expires_at=NOON_ISH + RENEW_WITHIN - timedelta(hours=1)), NOON_ISH)


def test_one_expiring_field_makes_the_agency_due():
    fields = _fields(expires_at=NOON_ISH + timedelta(days=90), only=RT_COVERAGE_FIELDS[1:])
    fields += (RecordedField(RT_COVERAGE_FIELDS[0], True, NOON_ISH - timedelta(minutes=1)),)
    assert needs_reprobe(fields, NOON_ISH)


def test_a_refuted_verdict_is_rechecked_before_it_expires():
    """A refutation blanks the agency's reports for as long as it stands, so
    it gets a fresh look every day rather than waiting out its expiry."""
    assert needs_reprobe(_fields(confirmed=False, expires_at=NOON_ISH + timedelta(days=90)), NOON_ISH)


def test_a_non_expiring_confirmed_verdict_is_never_due():
    assert not needs_reprobe(_fields(expires_at=None), NOON_ISH)


def test_a_non_expiring_refuted_verdict_is_due():
    assert needs_reprobe(_fields(confirmed=False, expires_at=None), NOON_ISH)


# ── time of day ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("hour", "minute", "inside"),
    [(0, 5, False), (6, 59, False), (7, 0, True), (12, 0, True), (19, 59, True), (20, 0, False), (23, 30, False)],
)
def test_the_service_window_is_read_on_the_agency_clock(hour, minute, inside):
    assert in_service_window(datetime(2026, 10, 27, hour, minute, tzinfo=JST), "Asia/Tokyo") is inside


def test_the_window_follows_the_agency_timezone_not_the_callers():
    # 11:00 JST is 22:00 the previous evening in New York.
    assert not in_service_window(NOON_ISH, "America/New_York")
    assert in_service_window(NOON_ISH.astimezone(timezone.utc), "Asia/Tokyo")


@pytest.mark.parametrize("tz", ["Not/AZone", "", "../etc/passwd"])
def test_an_unreadable_timezone_is_never_in_window(tz):
    assert not in_service_window(NOON_ISH, tz)


def test_selection_needs_both_a_due_verdict_and_the_window():
    fresh = _candidate(8)
    expired = _candidate(9, fields=_fields(expires_at=NOON_ISH - timedelta(days=1)))
    never = _candidate(10, fields=())
    abroad = _candidate(11, fields=(), tz="America/New_York")
    assert [c.agency_id for c in select_due([fresh, expired, never, abroad], NOON_ISH)] == [9, 10]


def test_nothing_is_selected_overnight():
    candidates = [_candidate(9, fields=()), _candidate(10, fields=_fields(confirmed=False))]
    assert select_due(candidates, datetime(2026, 10, 27, 0, 5, tzinfo=JST)) == []


# ── grouping the registry read ─────────────────────────────────────────────


def test_rows_group_into_one_candidate_per_agency():
    url8 = "https://feeds.example.jp/realtime/8/trip_updates.bin"
    url17 = "https://feeds.example.jp/realtime/17/trip_updates.bin"
    exp = NOON_ISH + timedelta(days=3)
    rows = [
        (8, url8, "Asia/Tokyo", "stop_id", True, exp),
        (8, url8, "Asia/Tokyo", "arr_delay", False, None),
        # The LEFT JOIN's row for an agency with no verdict at all.
        (17, url17, "Asia/Tokyo", None, None, None),
    ]
    assert group_candidates(rows) == [
        Candidate(
            8, url8, "Asia/Tokyo", (RecordedField("stop_id", True, exp), RecordedField("arr_delay", False, None))
        ),
        Candidate(17, url17, "Asia/Tokyo", ()),
    ]


# ── one run, end to end over stubs ─────────────────────────────────────────


def _cov(n: int) -> dict:
    return {
        "stop_time_updates": n,
        "feed_timestamp": 1_793_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.1,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }


class _Runs:
    """Stands in for `pipeline_runs.record_run`, keeping what each run closed with."""

    def __init__(self):
        self.closed: list[tuple[str, int | None, str, int | None]] = []

    @contextmanager
    def record_run(self, _conn, kind, agency_id=None, **_kw):
        handle = pipeline_runs.RunHandle(run_id=len(self.closed) + 1)
        try:
            yield handle
        except Exception:
            self.closed.append((kind, agency_id, "error", handle.rows))
            raise
        self.closed.append((kind, agency_id, handle.status, handle.rows))


@pytest.fixture
def run_env(monkeypatch):
    runs = _Runs()
    recorded: list[tuple[int, str, int]] = []
    candidates: list[Candidate] = []

    async def _record(_db_url, agency_id, feed_url, cov):
        recorded.append((agency_id, feed_url, cov["stop_time_updates"]))
        return {field: True for field in RT_COVERAGE_FIELDS}

    monkeypatch.setattr(pipeline_runs, "record_run", runs.record_run)
    monkeypatch.setattr(reprobe, "_record", _record)
    monkeypatch.setattr(reprobe, "load_candidates", lambda _conn: candidates)
    monkeypatch.setattr(reprobe, "field_coverage", lambda raw: _cov(int(raw)))
    return runs, recorded, candidates


def test_a_due_agency_is_fetched_measured_and_recorded(run_env):
    runs, recorded, candidates = run_env
    candidates.append(_candidate(9, fields=()))

    outcome = reprobe.reprobe_due_agencies(object(), "postgresql://x", now=NOON_ISH, fetch=lambda _url: b"120")

    assert recorded == [(9, candidates[0].feed_url, 120)]
    assert runs.closed == [("probe", 9, "ok", 120)]
    assert outcome.recorded == [9]
    assert outcome.skipped == []
    assert outcome.failed == []


def test_a_thin_poll_records_nothing_and_closes_its_run_skipped(run_env):
    """A poll this small routinely carries arrivals on half its updates,
    which reads as refuting arr_delay. Not evidence either way."""
    runs, recorded, candidates = run_env
    candidates.append(_candidate(54, fields=()))

    outcome = reprobe.reprobe_due_agencies(
        object(), "postgresql://x", now=NOON_ISH, fetch=lambda _url: str(MIN_SAMPLE - 1).encode()
    )

    assert recorded == []
    assert runs.closed == [("probe", 54, "skipped", MIN_SAMPLE - 1)]
    assert outcome.skipped == [54]


def test_a_poll_at_the_floor_is_evidence(run_env):
    _runs, recorded, candidates = run_env
    candidates.append(_candidate(54, fields=()))

    reprobe.reprobe_due_agencies(object(), "postgresql://x", now=NOON_ISH, fetch=lambda _url: str(MIN_SAMPLE).encode())

    assert [r[0] for r in recorded] == [54]


def test_one_failed_fetch_does_not_stop_the_next_agency(run_env):
    runs, _recorded, candidates = run_env
    candidates.extend([_candidate(9, fields=()), _candidate(10, fields=())])

    def _fetch(url):
        if "/9/" in url:
            raise OSError("connection reset")
        return b"300"

    outcome = reprobe.reprobe_due_agencies(object(), "postgresql://x", now=NOON_ISH, fetch=_fetch)

    assert runs.closed == [("probe", 9, "error", None), ("probe", 10, "ok", 300)]
    assert outcome.failed == [9]
    assert outcome.recorded == [10]


def test_a_feed_repointed_mid_probe_is_not_recorded(run_env, monkeypatch):
    runs, _recorded, candidates = run_env
    candidates.append(_candidate(9, fields=()))

    async def _repointed(*_a):
        return None

    monkeypatch.setattr(reprobe, "_record", _repointed)

    outcome = reprobe.reprobe_due_agencies(object(), "postgresql://x", now=NOON_ISH, fetch=lambda _url: b"300")

    assert runs.closed == [("probe", 9, "skipped", 300)]
    assert outcome.recorded == []
    assert outcome.skipped == [9]


def test_nothing_due_opens_no_run_and_fetches_nothing(run_env):
    runs, recorded, candidates = run_env
    candidates.append(_candidate(8))

    def _fetch(_url):
        raise AssertionError("a verdict that is not due must not be re-fetched")

    outcome = reprobe.reprobe_due_agencies(object(), "postgresql://x", now=NOON_ISH, fetch=_fetch)

    assert runs.closed == []
    assert recorded == []
    assert outcome == reprobe.ReprobeOutcome()


def test_an_overnight_run_opens_no_run_and_fetches_nothing(run_env):
    runs, _recorded, candidates = run_env
    candidates.append(_candidate(9, fields=()))

    def _fetch(_url):
        raise AssertionError("an overnight run must not fetch")

    reprobe.reprobe_due_agencies(object(), "postgresql://x", now=datetime(2026, 10, 27, 0, 5, tzinfo=JST), fetch=_fetch)

    assert runs.closed == []


def test_a_run_row_that_cannot_be_written_does_not_cost_the_verdict(monkeypatch):
    """A `pipeline_runs` that predates the `probe` kind refuses the row; the
    bookkeeping is lost and the verdict is still written."""
    recorded: list[int] = []

    async def _record(_db_url, agency_id, _feed_url, _cov):
        recorded.append(agency_id)
        return {field: True for field in RT_COVERAGE_FIELDS}

    monkeypatch.setattr(reprobe, "_record", _record)
    monkeypatch.setattr(reprobe, "load_candidates", lambda _conn: [_candidate(9, fields=())])
    monkeypatch.setattr(reprobe, "field_coverage", lambda raw: _cov(int(raw)))
    monkeypatch.setattr(pipeline_runs, "start_run", lambda *_a, **_k: None)

    outcome = reprobe.reprobe_due_agencies(object(), "postgresql://x", now=NOON_ISH, fetch=lambda _url: b"300")

    assert recorded == [9]
    assert outcome.recorded == [9]
