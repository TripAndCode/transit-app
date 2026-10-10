"""Training rows for a model that may see nothing after its cutoff day. Needs
the optional `ml` dependency group."""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from ml.features import build_frame, weekend_days

HORIZON_DAYS = 7


def training_targets(first_day: date, cutoff: date, *, seed: int, min_history_days: int = 7) -> dict[date, list[date]]:
    """For every target day up to the cutoff, one origin 1..HORIZON_DAYS days
    before it, so every past day is a target once. The lead is drawn at random
    rather than read off a weekly grid of origins: on a grid every run one day
    ahead falls on the same weekday, and a model can learn the weekday as the
    lead. An origin needs min_history_days of history behind it."""
    rng = random.Random(seed)
    earliest_origin = first_day + timedelta(days=min_history_days)
    by_origin: dict[date, list[date]] = defaultdict(list)
    day = earliest_origin + timedelta(days=1)
    while day <= cutoff:
        lead = rng.randint(1, min(HORIZON_DAYS, (day - earliest_origin).days))
        by_origin[day - timedelta(days=lead)].append(day)
        day += timedelta(days=1)
    return dict(sorted(by_origin.items()))


def training_frame(
    runs: pd.DataFrame, cutoff: date, *, window_days: int, half_life_days: float, seed: int = 7
) -> pd.DataFrame:
    """One forecast row per run up to the cutoff, each from its drawn origin,
    weighted so a target's weight halves every half_life_days back from the
    cutoff. Each agency's own history floors its own targets: in a multi-agency
    `runs`, a later-starting agency does not borrow an earlier agency's floor."""
    if runs.empty:
        return pd.DataFrame()
    targets_by_origin: dict[date, dict[int, list[date]]] = {}
    for agency_id, agency_runs in runs.groupby("agency_id"):
        first_day = agency_runs["service_date"].min().date()
        for origin, days in training_targets(first_day, cutoff, seed=seed).items():
            targets_by_origin.setdefault(origin, {})[agency_id] = days

    days_table = weekend_days(runs)
    frames = []
    for origin, by_agency in sorted(targets_by_origin.items()):
        frame = build_frame(
            runs, origin, window_days=window_days, horizon_days=HORIZON_DAYS, precomputed_weekend_days=days_table
        )
        parts = [
            frame[(frame["agency_id"] == agency_id) & frame["service_date"].isin(pd.to_datetime(days))]
            for agency_id, days in by_agency.items()
        ]
        kept = pd.concat(parts, ignore_index=True)
        if not kept.empty:
            frames.append(kept)
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    age_days = (pd.Timestamp(cutoff) - frame["service_date"]).dt.days
    frame["weight"] = (0.5 ** (age_days / half_life_days)).astype("float32")
    return frame
