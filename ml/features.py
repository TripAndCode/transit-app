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
_CLIP_BOUNDS = ["clip_lo", "clip_hi"]


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


# A template is copied from the agency's last two weeks. Each candidate is
# judged against the _PREVIOUS_DAYS days of its type before it, so the
# reference window reaches that many weeks behind the oldest candidate: a
# Saturday or a Sunday-holiday comes round once a week.
_PREVIOUS_DAYS = 3
_TEMPLATE_LOOKBACK_DAYS = 14
_REFERENCE_DAYS = _TEMPLATE_LOOKBACK_DAYS + 7 * _PREVIOUS_DAYS
# At the daily predict, the sync before it has ingested every closed JST day,
# so a current feed's newest day is origin-1 (short only of its after-midnight
# captures). A second day absorbs one sync or upload that has not landed yet;
# a newest observed day older than that means the feed or the replica stopped.
# Any observed run counts, so no timetable change can silence an agency.
_STALE_AFTER_DAYS = 2
# A template must look like the previous days of its type: a day with fewer
# than half their median slots, or whose first run starts or last run ends this
# many hours past their median first or last hour, is partial. A full day's ends
# wander by an hour or two. Judging against only the last few days lets a
# permanent timetable change become the norm once two days of it have run.
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
    by the weekday rule alone. A day is partial, and never a template, when
    against the median of the previous _PREVIOUS_DAYS days of its type it ran
    fewer than half the route x hour slots, or its span is cut off at either
    end; a day with no earlier day of its type is not partial. An agency whose
    newest day with any observed run is older than origin-_STALE_AFTER_DAYS
    gets no template at all, so a feed that stopped stops being forecast.

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
        observed = agency_runs.loc[agency_runs["service_date"] < t, "service_date"]
        if observed.empty or observed.max() < t - pd.Timedelta(days=_STALE_AFTER_DAYS):
            continue
        eligible = _template_days(agency_runs, t, types)
        recent = eligible[eligible.index >= t - pd.Timedelta(days=_TEMPLATE_LOOKBACK_DAYS)]
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


def _template_days(agency_runs: pd.DataFrame, origin: pd.Timestamp, types: DayTypes) -> pd.Series:
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
        previous = days[(kinds == kinds[day]) & (days.index < day)].tail(_PREVIOUS_DAYS)
        if previous.empty:
            keep.append(True)
            continue
        thin = own["slots"] < _PARTIAL_SHARE * previous["slots"].median()
        cut_off = (
            own["first"] >= previous["first"].median() + _CUT_OFF_HOURS
            or own["last"] <= previous["last"].median() - _CUT_OFF_HOURS
        )
        keep.append(not (thin or cut_off))
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
    clip_quantile: float | None = None,
) -> pd.DataFrame:
    """One row per run on origin+1..origin+horizon_days, with its features as of
    origin−1 and its actual delay. The origin day itself is neither history nor
    target. `runs` must carry `route_id` (with_route_ids). `precomputed_weekend_days`,
    when given, must come from `weekend_days()` on this same `runs`; it is filtered
    to origin−1 here either way, so passing it changes performance, not the result.

    `b0` is the slot's raw mean, the B0 baseline every variant is scored against,
    and `slot_p10`/`slot_p90` are B0's raw interval. With `clip_quantile` (q), the
    delay features are built from a winsorized history instead: each agency's
    window delays are clipped to that window's own (1−q, q) quantiles, carried as
    `clip_lo`/`clip_hi` so a model's training target can be bounded the same way.
    The bounds come only from the window, so they never read the origin day or
    later, and never depend on another agency's runs."""
    check_clip_quantile(clip_quantile)
    clipped = clip_quantile is not None
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
        for column in [*_HISTORY, "b0", *(_CLIP_BOUNDS if clipped else [])]:
            frame[column] = np.nan
    else:
        agency_days = hist.groupby("agency_id")["service_date"].nunique().rename("agency_days")
        features_hist = hist
        if clip_quantile is not None:
            bounds = _clip_bounds(hist, clip_quantile)
            features_hist = _winsorized(hist, bounds)
            frame = frame.join(bounds, on="agency_id")
        frame = (
            frame.join(_slot_stats(features_hist, hist if clipped else None), on=SLOT)
            .join(_route_stats(features_hist, last), on=ROUTE)
            .join(_trip_stats(features_hist), on=TRIP)
            .join(agency_days, on="agency_id")
        )
    all_days = precomputed_weekend_days if precomputed_weekend_days is not None else weekend_days(runs)
    days = all_days[all_days["service_date"] <= last]
    if days.empty:
        frame["service_weekend_share"] = np.nan
    else:
        frame = frame.join(_weekend_share(days), on=["agency_id", "service"])

    frame[_COUNTS] = frame[_COUNTS].fillna(0)
    extra = ["b0", "slot_p10", "slot_p90", *(_CLIP_BOUNDS if clipped else [])]
    numeric = [column for column in [*FEATURES, *extra] if column not in CATEGORICAL]
    frame[numeric] = frame[numeric].astype("float32")
    return frame.reset_index(drop=True)


def check_clip_quantile(quantile: float | None) -> None:
    if quantile is not None and not 0.5 < quantile < 1:
        raise ValueError(f"clip_quantile must lie strictly between 0.5 and 1, not {quantile}")


def _clip_bounds(hist: pd.DataFrame, quantile: float) -> pd.DataFrame:
    bounds = hist.groupby("agency_id")["delay_min"].quantile([1 - quantile, quantile]).unstack()
    bounds.columns = _CLIP_BOUNDS
    return bounds


def _winsorized(hist: pd.DataFrame, bounds: pd.DataFrame) -> pd.DataFrame:
    lo = hist["agency_id"].map(bounds["clip_lo"])
    hi = hist["agency_id"].map(bounds["clip_hi"])
    return hist.assign(delay_min=hist["delay_min"].clip(lower=lo, upper=hi).astype("float32"))


def _slot_stats(hist: pd.DataFrame, raw: pd.DataFrame | None = None) -> pd.DataFrame:
    """B0 (the slot's runs-weighted mean), B1 (its latest day) and the slot's
    spread over the window, as features. `b0`, `slot_p10` and `slot_p90` are B0
    and its own empirical interval, always from the raw history (`raw`, when
    `hist` has been winsorized): computed and typed alongside the features but
    left out of FEATURES, so a caller can score B0 straight from this frame."""
    grouped = hist.groupby(SLOT)["delay_min"]
    stats = grouped.agg(slot_mean="mean", slot_runs="size", slot_std="std")
    raw_grouped = grouped if raw is None else raw.groupby(SLOT)["delay_min"]
    stats["b0"] = stats["slot_mean"] if raw is None else raw_grouped.mean()
    bounds = raw_grouped.quantile([0.1, 0.9]).unstack()
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
