"""Pure shaping helpers behind ``GET /api/admin/board``.

Everything here is a total function of its arguments — the window's anchor
day, the collectors' ``now``, and already-fetched rows. No clock, no DB, no
subprocess, so the board's states are reproducible and unit-testable, and the
router stays a thin "fetch, degrade, shape" layer.

Two invariants the helpers encode:

- **Only completed days count.** The heatmap window ends on *yesterday* (JST),
  matching ``pipeline.freshness``: today is still being ingested, so an
  incomplete aggregate for it is the normal healthy case, not a defect. A
  board that painted today amber every morning would train operators to
  ignore amber.
- **A day is fresh only if the agency was analyzed after that day ended.** A
  day whose data exists but was last aggregated before the day closed is
  ``stale``, because later arrivals for it were never folded in.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

BOARD_WINDOW_DAYS = 14
COLLECTOR_HISTORY_HOURS = 24

#: A day's clamped (implausible-delay) share above which the board raises a
#: data-quality alert. Clamping a handful of rows is routine; a percent of a
#: day's samples means the upstream feed is reporting something wrong.
CLAMP_ALERT_PCT = 1.0

_JST = ZoneInfo("Asia/Tokyo")

#: Tile order on the board, reading left to right. Fixed here rather than
#: taken from the collectors' own order so a collector that fails to run at
#: all still holds its place instead of shifting the other three sideways.
COLLECTOR_ORDER: tuple[str, ...] = ("oracle_crawler", "r2", "vps_loop", "github")

#: Fallback display names. The UI prefers its own ``admin.board.collector.*``
#: translations and falls back to these, so a collector added upstream before
#: the frontend knows about it still renders with a readable name.
COLLECTOR_LABELS: Mapping[str, str] = {
    "oracle_crawler": "Oracle crawler",
    "r2": "R2 sync",
    "vps_loop": "VPS loop",
    "github": "CI (GitHub)",
}

#: `scripts/ops_status.py` contract state -> board tile status. `stale` and
#: `degraded` both mean "running, but behind" and share one amber tile;
#: `failed` is the only state the board paints as down.
_TILE_STATUS: Mapping[str, str] = {
    "healthy": "ok",
    "degraded": "warn",
    "stale": "warn",
    "failed": "down",
    "unknown": "unknown",
}


def board_window(today: date) -> list[date]:
    """The ``BOARD_WINDOW_DAYS`` completed days ending yesterday, ascending."""
    return [today - timedelta(days=n) for n in range(BOARD_WINDOW_DAYS, 0, -1)]


def _jst_date(value: Any) -> date | None:
    """The JST calendar day of a timestamp. A naive timestamp is read as UTC,
    matching asyncpg's own handling of a ``timestamptz`` column."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_JST).date()


def board_freshness(rows: Iterable[Mapping[str, Any]], today: date) -> list[dict[str, Any]]:
    """Shape per-agency-per-day aggregate coverage into heatmap rows.

    ``rows`` is the flat join of agencies x ``agg_meta`` x ``agg_feed_health``:
    one row per agency per day that has feed-health data, plus a single
    ``date=None`` row for an agency with no data in range at all. Every
    agency gets exactly ``BOARD_WINDOW_DAYS`` cells regardless.
    """
    agencies: dict[int, dict[str, Any]] = {}
    for row in rows:
        aid = row["agency_id"]
        entry = agencies.setdefault(
            aid,
            {"agency_id": aid, "agency_name": row["agency_name"], "analyzed_at": row.get("analyzed_at"), "days": {}},
        )
        day = row.get("date")
        if day is not None:
            entry["days"][day] = row

    window = board_window(today)
    out: list[dict[str, Any]] = []
    for aid in sorted(agencies):
        entry = agencies[aid]
        analyzed_on = _jst_date(entry["analyzed_at"])
        days: list[dict[str, Any]] = []
        for day in window:
            row = entry["days"].get(day)
            raw = int(row["raw_samples"] or 0) if row is not None else 0
            if raw <= 0:
                days.append({"date": day.isoformat(), "state": "missing", "clamp_pct": None})
                continue
            clamp = int((row or {}).get("clamp_count") or 0)
            state = "fresh" if analyzed_on is not None and analyzed_on > day else "stale"
            days.append({"date": day.isoformat(), "state": state, "clamp_pct": round(clamp * 100 / raw, 2)})
        out.append({"agency_id": aid, "agency_name": entry["agency_name"], "days": days})
    return out


def _trailing_non_fresh(days: Sequence[Mapping[str, Any]]) -> int:
    """How many of the most recent completed days are not aggregated. One is
    already more than 24 h behind, since the window holds completed days only."""
    count = 0
    for day in reversed(days):
        if day["state"] == "fresh":
            break
        count += 1
    return count


def board_alerts(
    *,
    freshness: Sequence[Mapping[str, Any]],
    migrations: Any,
    pending_llm_approvals: int,
) -> list[dict[str, Any]]:
    """Derive the board's alert list from the already-shaped board data.

    Each alert carries a ``code`` plus ``params`` for the UI to translate, and
    a plain ``text`` summary for consumers with no locale (logs, exports).
    ``migrations`` is a ``pipeline.health.MigrationStatus`` or ``None`` when
    that check could not run — an unavailable check never invents an alert.
    """
    alerts: list[dict[str, Any]] = []

    for row in freshness:
        behind = _trailing_non_fresh(row["days"])
        if behind:
            alerts.append(
                {
                    "level": "warn",
                    "code": "agency_stale",
                    "params": {"agency": row["agency_name"], "days": behind},
                    "text": f"{row['agency_name']}: aggregates {behind} day(s) behind",
                    "href": "/admin/ops",
                }
            )

    for row in freshness:
        scored = [d for d in row["days"] if d["clamp_pct"] is not None]
        worst = max(scored, key=lambda d: d["clamp_pct"], default=None)
        if worst is not None and worst["clamp_pct"] > CLAMP_ALERT_PCT:
            alerts.append(
                {
                    "level": "warn",
                    "code": "clamp_high",
                    "params": {"agency": row["agency_name"], "pct": worst["clamp_pct"], "date": worst["date"]},
                    "text": f"{row['agency_name']}: {worst['clamp_pct']}% of samples clamped on {worst['date']}",
                    "href": "/admin/ops",
                }
            )

    behind_migrations = getattr(migrations, "behind", 0) if migrations is not None else 0
    if behind_migrations:
        alerts.append(
            {
                "level": "warn",
                "code": "migrations_behind",
                "params": {"count": behind_migrations, "latest": getattr(migrations, "latest", None)},
                "text": f"Schema behind by {behind_migrations} migration(s)",
                "href": "/admin/ops",
            }
        )

    if pending_llm_approvals > 0:
        alerts.append(
            {
                "level": "info",
                "code": "llm_approvals_pending",
                "params": {"count": pending_llm_approvals},
                "text": f"{pending_llm_approvals} user(s) awaiting AI access approval",
                "href": "/admin/users",
            }
        )

    return alerts


def _parse_iso(value: Any) -> datetime | None:
    """Parse a collector document's ISO timestamp. Returns ``None`` for
    anything unparseable — a malformed collector field degrades one tile's
    sparkline, never the request."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def collector_history(
    last_success_at: datetime | None, now: datetime, hours: int = COLLECTOR_HISTORY_HOURS
) -> list[int]:
    """A ``hours``-cell "known good through" sparkline, oldest cell first.

    The status contract records only the last success, not a per-run log, so a
    cell means "the component was still succeeding when this hour began" — the
    honest signal available — rather than a fabricated per-hour outcome.
    """
    if last_success_at is None:
        return [0] * hours
    first_bucket = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)
    return [1 if last_success_at >= first_bucket + timedelta(hours=i) else 0 for i in range(hours)]


def collector_tiles(
    documents: Iterable[Mapping[str, Any]],
    now: datetime,
    reasons: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One tile per ``COLLECTOR_ORDER`` entry, in that order.

    A collector missing from ``documents`` — because ``collect_all`` itself
    timed out or blew up — still gets an ``unknown`` tile, so the board's shape
    never depends on whether the collectors ran.
    """
    by_key = {doc.get("component"): doc for doc in documents}
    reasons = reasons or {}
    tiles: list[dict[str, Any]] = []
    for key in COLLECTOR_ORDER:
        doc = by_key.get(key) or {}
        last_success_at = doc.get("last_success_at")
        tiles.append(
            {
                "key": key,
                "label": COLLECTOR_LABELS.get(key, key),
                "status": _TILE_STATUS.get(str(doc.get("state") or "unknown"), "unknown"),
                "last_success_at": last_success_at if isinstance(last_success_at, str) else None,
                "detail": reasons.get(key),
                "history": collector_history(_parse_iso(last_success_at), now),
            }
        )
    return tiles
