"""Training rows for a model that may see nothing after its cutoff day. Needs
the optional `ml` dependency group."""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from ml.features import build_frame

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
    weighted so a target's weight halves every half_life_days back from the cutoff."""
    if runs.empty:
        return pd.DataFrame()
    first_day = runs["service_date"].min().date()
    frames = []
    for origin, days in training_targets(first_day, cutoff, seed=seed).items():
        frame = build_frame(runs, origin, window_days=window_days, horizon_days=HORIZON_DAYS)
        frame = frame[frame["service_date"].isin(pd.to_datetime(days))]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    age_days = (pd.Timestamp(cutoff) - frame["service_date"]).dt.days
    frame["weight"] = (0.5 ** (age_days / half_life_days)).astype("float32")
    return frame
