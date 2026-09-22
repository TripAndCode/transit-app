"""Pure shaping helpers behind `GET /api/admin/board` (api/admin_board.py).

No DB, no collectors, no clock — every helper takes its `today`/`now` as an
argument so the board's states are reproducible.
"""

from datetime import date, datetime, timezone

import pytest

from api.admin_board import (
    BOARD_WINDOW_DAYS,
    CLAMP_ALERT_PCT,
    COLLECTOR_ORDER,
    board_alerts,
    board_freshness,
    board_window,
    collector_history,
    collector_tiles,
)

TODAY = date(2026, 9, 20)
# Analyzed after 09-18 ended, so 09-18 and earlier are fresh, 09-19 is not.
ANALYZED_AT = datetime(2026, 9, 19, 2, 0, tzinfo=timezone.utc)


def _row(day: date | None, *, raw: int = 1000, clamp: int = 0, aid: int = 1, name: str = "Hokuriku"):
    return {
        "agency_id": aid,
        "agency_name": name,
        "analyzed_at": ANALYZED_AT,
        "date": day,
        "raw_samples": raw,
        "clamp_count": clamp,
    }


# ── board_window ─────────────────────────────────────────────────────────


def test_window_is_fourteen_complete_days_ending_yesterday():
    window = board_window(TODAY)
    assert len(window) == BOARD_WINDOW_DAYS
    assert window[0] == date(2026, 9, 6)
    assert window[-1] == date(2026, 9, 19)
    assert window == sorted(window)


# ── board_freshness ──────────────────────────────────────────────────────


def test_freshness_emits_one_cell_per_window_day_even_with_no_rows():
    out = board_freshness([_row(None)], TODAY)
    assert len(out) == 1
    assert out[0]["agency_id"] == 1
    assert out[0]["agency_name"] == "Hokuriku"
    assert [d["state"] for d in out[0]["days"]] == ["missing"] * BOARD_WINDOW_DAYS
    assert out[0]["days"][0]["date"] == "2026-09-06"


def test_day_analyzed_after_it_ended_is_fresh_and_a_later_day_is_stale():
    out = board_freshness([_row(date(2026, 9, 18)), _row(date(2026, 9, 19))], TODAY)
    days = {d["date"]: d["state"] for d in out[0]["days"]}
    assert days["2026-09-18"] == "fresh"
    assert days["2026-09-19"] == "stale"


def test_never_analyzed_agency_has_no_fresh_days():
    rows = [{**_row(date(2026, 9, 18)), "analyzed_at": None}]
    out = board_freshness(rows, TODAY)
    assert {d["state"] for d in out[0]["days"]} == {"missing", "stale"}


def test_zero_raw_samples_is_missing_not_fresh():
    out = board_freshness([_row(date(2026, 9, 18), raw=0)], TODAY)
    days = {d["date"]: d for d in out[0]["days"]}
    assert days["2026-09-18"]["state"] == "missing"
    assert days["2026-09-18"]["clamp_pct"] is None


def test_clamp_pct_is_a_percentage_of_that_days_raw_samples():
    out = board_freshness([_row(date(2026, 9, 18), raw=10_000, clamp=149)], TODAY)
    days = {d["date"]: d for d in out[0]["days"]}
    assert days["2026-09-18"]["clamp_pct"] == pytest.approx(1.49)


def test_days_outside_the_window_are_dropped():
    out = board_freshness([_row(date(2026, 1, 1)), _row(TODAY)], TODAY)
    assert [d["state"] for d in out[0]["days"]] == ["missing"] * BOARD_WINDOW_DAYS


def test_agencies_are_ordered_by_id_and_kept_distinct():
    rows = [
        _row(date(2026, 9, 18), aid=7, name="Noto"),
        _row(date(2026, 9, 18), aid=2, name="Kaga"),
    ]
    out = board_freshness(rows, TODAY)
    assert [r["agency_id"] for r in out] == [2, 7]


# ── board_alerts ─────────────────────────────────────────────────────────


class _Migrations:
    def __init__(self, behind: int, latest: str | None = "0053"):
        self.behind = behind
        self.latest = latest
        self.applied = "0053"


def _fresh_agency(states: list[str], clamps: list[float | None] | None = None):
    window = board_window(TODAY)
    clamps = clamps or [0.0] * len(states)
    return {
        "agency_id": 1,
        "agency_name": "Hokuriku",
        "days": [
            {"date": d.isoformat(), "state": s, "clamp_pct": c} for d, s, c in zip(window, states, clamps, strict=True)
        ],
    }


def test_no_alerts_when_everything_is_fresh_and_current():
    alerts = board_alerts(
        freshness=[_fresh_agency(["fresh"] * BOARD_WINDOW_DAYS)],
        migrations=_Migrations(0),
        pending_llm_approvals=0,
    )
    assert alerts == []


def test_trailing_non_fresh_days_raise_one_warn_per_agency():
    states = ["fresh"] * (BOARD_WINDOW_DAYS - 3) + ["stale", "missing", "missing"]
    alerts = board_alerts(freshness=[_fresh_agency(states)], migrations=_Migrations(0), pending_llm_approvals=0)
    assert len(alerts) == 1
    assert alerts[0]["level"] == "warn"
    assert alerts[0]["code"] == "agency_stale"
    assert alerts[0]["params"]["days"] == 3
    assert alerts[0]["params"]["agency"] == "Hokuriku"
    assert alerts[0]["href"] == "/admin/ops"
    assert "Hokuriku" in alerts[0]["text"]


def test_a_gap_that_is_not_trailing_does_not_alert():
    states = ["fresh"] * 5 + ["missing"] * 2 + ["fresh"] * (BOARD_WINDOW_DAYS - 7)
    alerts = board_alerts(freshness=[_fresh_agency(states)], migrations=_Migrations(0), pending_llm_approvals=0)
    assert alerts == []


def test_clamp_above_the_threshold_alerts_on_the_worst_day():
    clamps: list[float | None] = [0.1] * BOARD_WINDOW_DAYS
    clamps[4] = CLAMP_ALERT_PCT + 0.49
    alerts = board_alerts(
        freshness=[_fresh_agency(["fresh"] * BOARD_WINDOW_DAYS, clamps)],
        migrations=_Migrations(0),
        pending_llm_approvals=0,
    )
    assert [a["code"] for a in alerts] == ["clamp_high"]
    assert alerts[0]["params"]["pct"] == pytest.approx(CLAMP_ALERT_PCT + 0.49)
    assert alerts[0]["params"]["date"] == board_window(TODAY)[4].isoformat()


def test_clamp_exactly_at_the_threshold_does_not_alert():
    clamps: list[float | None] = [CLAMP_ALERT_PCT] * BOARD_WINDOW_DAYS
    alerts = board_alerts(
        freshness=[_fresh_agency(["fresh"] * BOARD_WINDOW_DAYS, clamps)],
        migrations=_Migrations(0),
        pending_llm_approvals=0,
    )
    assert alerts == []


def test_migrations_behind_and_pending_approvals_alert():
    alerts = board_alerts(freshness=[], migrations=_Migrations(2), pending_llm_approvals=3)
    assert [(a["code"], a["level"]) for a in alerts] == [
        ("migrations_behind", "warn"),
        ("llm_approvals_pending", "info"),
    ]
    assert alerts[0]["params"]["count"] == 2
    assert alerts[1]["params"]["count"] == 3
    assert alerts[1]["href"] == "/admin/users"


def test_unavailable_migration_status_raises_no_migration_alert():
    assert board_alerts(freshness=[], migrations=None, pending_llm_approvals=0) == []


# ── collector tiles ──────────────────────────────────────────────────────

NOW = datetime(2026, 9, 20, 8, 12, tzinfo=timezone.utc)


def test_history_is_all_zero_without_a_recorded_success():
    assert collector_history(None, NOW) == [0] * 24


def test_history_marks_hours_up_to_the_last_success():
    # Last success 08:05 -> every hour bucket through the current one is good.
    assert collector_history(datetime(2026, 9, 20, 8, 5, tzinfo=timezone.utc), NOW) == [1] * 24


def test_history_drops_the_hours_since_the_last_success():
    # Last success 05:30 -> 06, 07 and the current 08 bucket are not covered.
    hist = collector_history(datetime(2026, 9, 20, 5, 30, tzinfo=timezone.utc), NOW)
    assert hist[-3:] == [0, 0, 0]
    assert hist[:-3] == [1] * 21


def test_history_older_than_the_window_is_all_zero():
    assert collector_history(datetime(2026, 9, 1, tzinfo=timezone.utc), NOW) == [0] * 24


def test_tiles_always_cover_every_collector_even_with_no_documents():
    tiles = collector_tiles([], NOW)
    assert [t["key"] for t in tiles] == list(COLLECTOR_ORDER)
    assert {t["status"] for t in tiles} == {"unknown"}
    assert all(t["history"] == [0] * 24 for t in tiles)
    assert all(t["label"] for t in tiles)


@pytest.mark.parametrize(
    ("state", "status"),
    [("healthy", "ok"), ("degraded", "warn"), ("stale", "warn"), ("failed", "down"), ("unknown", "unknown")],
)
def test_collector_state_maps_onto_the_tile_status(state, status):
    docs = [{"component": "r2", "state": state, "last_success_at": None, "details": {}}]
    tiles = {t["key"]: t for t in collector_tiles(docs, NOW)}
    assert tiles["r2"]["status"] == status


def test_tile_carries_the_last_success_and_its_reason():
    docs = [
        {
            "component": "oracle_crawler",
            "state": "degraded",
            "last_success_at": "2026-09-20T08:05:00Z",
            "details": {},
        }
    ]
    tiles = {t["key"]: t for t in collector_tiles(docs, NOW, reasons={"oracle_crawler": "RT feed is degraded"})}
    tile = tiles["oracle_crawler"]
    assert tile["last_success_at"] == "2026-09-20T08:05:00Z"
    assert tile["detail"] == "RT feed is degraded"
    assert tile["history"] == [1] * 24


def test_unparseable_last_success_degrades_to_an_empty_history():
    docs = [{"component": "github", "state": "healthy", "last_success_at": "not-a-timestamp", "details": {}}]
    tiles = {t["key"]: t for t in collector_tiles(docs, NOW)}
    assert tiles["github"]["history"] == [0] * 24
    assert tiles["github"]["status"] == "ok"
