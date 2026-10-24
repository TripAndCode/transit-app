"""Model features for a forecast made on origin T. Every value comes from runs on
or before T−1, except what the timetable fixes in advance about the run being
forecast: its route, trip, service, scheduled hour and date. Needs the optional
`ml` dependency group."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

import numpy as np
import pandas as pd

from pipeline.day_types import DayTypes, public_holidays_between, resolve_day_types

CATEGORICAL = ["agency_id", "route_id"]
FEATURES = [
    "agency_id",
    "route_id",
    "hour",
    "hour_sin",
    "hour_cos",
    "weekday",
    "h",
    "service_weekend_share",
    "slot_mean",
    "slot_runs",
    "slot_std",
    "slot_days",
    "slot_last",
    "route_mean",
    "route_mean_7",
    "route_mean_3",
    "route_trend",
    "route_prev",
    "trip_mean",
    "trip_runs",
    "trip_stops",
    "trip_span",
    "agency_days",
]
TARGET = "delay_min"
SLOT = ["agency_id", "route_code", "weekday", "hour"]
ROUTE = ["agency_id", "route_code"]
TRIP = ["agency_id", "trip_id"]
_HISTORY = [
    "slot_mean",
    "slot_runs",
    "slot_std",
    "slot_days",
    "slot_p10",
    "slot_p90",
    "slot_last",
    "route_mean",
    "route_mean_7",
    "route_mean_3",
    "route_prev",
    "route_trend",
    "trip_mean",
    "trip_runs",
    "trip_stops",
    "trip_span",
    "agency_days",
]
_COUNTS = ["slot_runs", "slot_days", "trip_runs", "agency_days"]


def route_ids(runs: pd.DataFrame) -> pd.DataFrame:
    """A deterministic integer per (agency, route) in this table's vocabulary, the
    categorical code the models split on. A route's identity is known in advance,
    so the vocabulary may span the whole table without leaking anything — but the
    same vocabulary table must be reused at inference time rather than rebuilt
    from a different `runs` snapshot, or a route's id shifts with the sort order."""
    keys = runs[ROUTE].drop_duplicates().sort_values(ROUTE).reset_index(drop=True)
    return keys.assign(route_id=np.arange(len(keys), dtype="int32"))


def with_route_ids(runs: pd.DataFrame, ids: pd.DataFrame) -> pd.DataFrame:
    return runs.merge(ids, on=ROUTE, how="left", validate="many_to_one")


# A template is copied from the agency's last two weeks, but whether a day is
# partial is judged against four: two weeks hold only two Saturdays, too few
# for a typical count that one fragment cannot drag down.
_TEMPLATE_LOOKBACK_DAYS = 14
_REFERENCE_DAYS = 28
# At the daily predict, the sync before it has ingested every closed JST day,
# so a current feed's newest day is origin-1 (short only of its after-midnight
# captures). A second day absorbs one sync or upload that has not landed yet;
# a newest complete day older than that means the feed or the replica stopped.
_STALE_AFTER_DAYS = 2
# A partial day is short of slots AND cut off: its first run starts, or its last
# run ends, this many hours past the agency's typical one for its day type. A
# stopped feed or a missing archive cuts a day's span; a timetable cut thins
# the day but keeps its first and last runs, and a full day's ends wander by an
# hour or two.
_PARTIAL_SHARE = 0.5
_CUT_OFF_HOURS = 4


def template_span(origin: date, horizon_days: int = 7) -> tuple[date, date]:
    """The dates template_future_runs reads a day type for: its reference window
    through the last target day. A caller's `day_types` must cover them."""
    return origin - timedelta(days=_REFERENCE_DAYS), origin + timedelta(days=horizon_days)


def template_future_runs(
    runs: pd.DataFrame,
    origin: date,
    horizon_days: int = 7,
    *,
    day_types: Mapping[int, DayTypes] | None = None,
) -> pd.DataFrame:
    """Live prediction has no observed runs after `origin` -- `runs` holds only
    completed trips, and `origin` itself can already hold a same-day partial
    row (GTFS-RT ingestion is continuous, so the morning's trips may already
    be in ClickHouse by the time the daily predict job runs). Each agency
    templates its own origin+1..origin+horizon_days from the most recent
    complete day STRICTLY BEFORE origin, in its own trailing
    _TEMPLATE_LOOKBACK_DAYS of history, of the target day's day type (weekday,
    Saturday, Sunday-or-holiday) -- never another agency's day (multi-agency
    `runs` can have agencies at different freshness), never origin's own
    still-incomplete day, and never arbitrarily old history. A target day with
    no such day is skipped, never matched by weekday instead. Only the realized
    delay is actually unknown for a row that is templated, so it is NaN.

    `day_types` holds each agency's types over template_span (its calendar,
    from pipeline.day_types.load_day_types); an agency absent from it is typed
    by the weekday rule alone. A day is partial when it ran fewer than half the
    route x hour slots of the median of the agency's other days of its type in
    the reference window, so a fragment never votes on what is typical, and its
    span is cut off at either end against those days' median first and last
    hour. An agency whose newest complete day is older than
    origin-_STALE_AFTER_DAYS gets no template at all, so a feed that stopped
    stops being forecast.

    A day the weekday rule alone typed (no calendar, or one that does not cover
    it: the replica keeps only each feed's current calendar) is never a
    template when it is a public holiday or in the 12/29-1/3 year-end break.
    Feeds run some holidays on their weekday timetable and the year-end break
    on a holiday one, so the rule's type for such a day is a guess, while the
    target it would be copied to may be typed by the feed's calendar."""
    t = pd.Timestamp(origin)
    first, last = template_span(origin, horizon_days)
    weekday_rule = resolve_day_types(first, last, [], [], {})
    holidays = frozenset(public_holidays_between(first, last))
    frames = []
    for agency_id, agency_runs in runs.groupby("agency_id"):
        types = (day_types or {}).get(int(agency_id), weekday_rule)
        complete = _complete_days(agency_runs, t, types)
        if complete.empty or complete.index.max() < t - pd.Timedelta(days=_STALE_AFTER_DAYS):
            continue
        recent = complete[complete.index >= t - pd.Timedelta(days=_TEMPLATE_LOOKBACK_DAYS)]
        candidates = recent[[not _guessed(day.date(), types, holidays) for day in recent.index]]
        for d in range(1, horizon_days + 1):
            target_day = t + pd.Timedelta(days=d)
            same_type = candidates[candidates == types.of(target_day.date())]
            if same_type.empty:
                continue
            template_day = same_type.index.max()
            rows = agency_runs[agency_runs["service_date"] == template_day].copy()
            rows["service_date"] = target_day
            rows["delay_min"] = np.nan
            frames.append(rows)
    return pd.concat(frames, ignore_index=True) if frames else runs.iloc[0:0].copy()


def _guessed(day: date, types: DayTypes, holidays: frozenset[date]) -> bool:
    year_end = (day.month == 12 and day.day >= 29) or (day.month == 1 and day.day <= 3)
    return day not in types.from_calendar and (day in holidays or year_end)


def _complete_days(agency_runs: pd.DataFrame, origin: pd.Timestamp, types: DayTypes) -> pd.Series:
    """The agency's days in the reference window before origin that are not
    partial, indexed by date, each with its day type."""
    window = agency_runs[
        (agency_runs["service_date"] >= origin - pd.Timedelta(days=_REFERENCE_DAYS))
        & (agency_runs["service_date"] < origin)
    ]
    days = (
        window[["service_date", "route_code", "hour"]]
        .drop_duplicates()
        .groupby("service_date")["hour"]
        .agg(slots="size", first="min", last="max")
    )
    kinds = pd.Series([types.of(day.date()) for day in days.index], index=days.index, dtype="object")
    keep = []
    for day, own in days.iterrows():
        others = days[(kinds == kinds[day]) & (days.index != day)]
        thin = not others.empty and own["slots"] < _PARTIAL_SHARE * others["slots"].median()
        cut_off = (
            own["first"] >= others["first"].median() + _CUT_OFF_HOURS
            or own["last"] <= others["last"].median() - _CUT_OFF_HOURS
        )
        keep.append(not (thin and cut_off))
    return kinds[keep]


def weekend_days(runs: pd.DataFrame) -> pd.DataFrame:
    """Each service's distinct days, deduplicated once, with a weekend flag. A
    caller that builds many origins from the same `runs` (training_frame) passes
    this into `build_frame` so the dedup — unlike the window-bounded slot/route/
    trip stats, it would otherwise scan all of `runs` on every origin — runs once
    rather than once per origin."""
    days = runs[["agency_id", "service", "service_date"]].drop_duplicates()
    return days.assign(weekend=(days["service_date"].dt.dayofweek >= 5).astype("float64"))


def build_frame(
    runs: pd.DataFrame,
    origin: date,
    *,
    window_days: int = 28,
    horizon_days: int = 7,
    precomputed_weekend_days: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per run on origin+1..origin+horizon_days, with its features as of
    origin−1 and its actual delay. The origin day itself is neither history nor
    target. `runs` must carry `route_id` (with_route_ids). `precomputed_weekend_days`,
    when given, must come from `weekend_days()` on this same `runs`; it is filtered
    to origin−1 here either way, so passing it changes performance, not the result."""
    t = pd.Timestamp(origin)
    last = t - pd.Timedelta(days=1)
    hist = runs[(runs["service_date"] <= last) & (runs["service_date"] >= t - pd.Timedelta(days=window_days))]
    hist = hist.assign(weekday=hist["service_date"].dt.dayofweek.astype("int16"))
    upcoming = (runs["service_date"] > t) & (runs["service_date"] <= t + pd.Timedelta(days=horizon_days))
    frame = runs[upcoming].copy()
    frame["origin"] = t
    frame["h"] = (frame["service_date"] - t).dt.days
    frame["weekday"] = frame["service_date"].dt.dayofweek.astype("int16")
    angle = 2 * np.pi * frame["hour"].astype("float64") / 24
    frame["hour_sin"] = np.sin(angle)
    frame["hour_cos"] = np.cos(angle)

    if hist.empty:
        for column in _HISTORY:
            frame[column] = np.nan
    else:
        agency_days = hist.groupby("agency_id")["service_date"].nunique().rename("agency_days")
        frame = (
            frame.join(_slot_stats(hist), on=SLOT)
            .join(_route_stats(hist, last), on=ROUTE)
            .join(_trip_stats(hist), on=TRIP)
            .join(agency_days, on="agency_id")
        )
    all_days = precomputed_weekend_days if precomputed_weekend_days is not None else weekend_days(runs)
    days = all_days[all_days["service_date"] <= last]
    if days.empty:
        frame["service_weekend_share"] = np.nan
    else:
        frame = frame.join(_weekend_share(days), on=["agency_id", "service"])

    frame[_COUNTS] = frame[_COUNTS].fillna(0)
    numeric = [column for column in [*FEATURES, "slot_p10", "slot_p90"] if column not in CATEGORICAL]
    frame[numeric] = frame[numeric].astype("float32")
    return frame.reset_index(drop=True)


def _slot_stats(hist: pd.DataFrame) -> pd.DataFrame:
    """B0 (the slot's runs-weighted mean), B1 (its latest day) and the slot's
    spread over the window. p10/p90 are B0's own empirical interval; computed
    and typed alongside the other features but left out of FEATURES, available
    to a caller that wants a per-slot interval directly from this frame."""
    grouped = hist.groupby(SLOT)["delay_min"]
    stats = grouped.agg(slot_mean="mean", slot_runs="size", slot_std="std")
    bounds = grouped.quantile([0.1, 0.9]).unstack()
    stats["slot_p10"] = bounds[0.1]
    stats["slot_p90"] = bounds[0.9]
    daily = hist.groupby([*SLOT, "service_date"])["delay_min"].mean().reset_index()
    stats["slot_days"] = daily.groupby(SLOT).size()
    stats["slot_last"] = daily.groupby(SLOT)["delay_min"].last()
    return stats


def _route_stats(hist: pd.DataFrame, last: pd.Timestamp) -> pd.DataFrame:
    stats = hist.groupby(ROUTE)["delay_min"].mean().rename("route_mean").to_frame()
    for days, name in ((7, "route_mean_7"), (3, "route_mean_3"), (1, "route_prev")):
        recent = hist[hist["service_date"] > last - pd.Timedelta(days=days)]
        stats[name] = recent.groupby(ROUTE)["delay_min"].mean()
    stats["route_trend"] = stats["route_mean_3"] - stats["route_mean"]
    return stats


def _trip_stats(hist: pd.DataFrame) -> pd.DataFrame:
    return hist.groupby(TRIP).agg(
        trip_mean=("delay_min", "mean"),
        trip_runs=("delay_min", "size"),
        trip_stops=("stops", "median"),
        trip_span=("span_min", "median"),
    )


def _weekend_share(days: pd.DataFrame) -> pd.Series:
    """The share of the days a service has run that fell on a Saturday or Sunday.
    A weekend service on a weekday is a holiday timetable. `days` is a
    weekend_days() table already filtered to the origin's history cutoff."""
    return days.groupby(["agency_id", "service"])["weekend"].mean().rename("service_weekend_share")
