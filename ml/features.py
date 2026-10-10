"""Model features for a forecast made on origin T. Every value comes from runs on
or before T−1, except what the timetable fixes in advance about the run being
forecast: its route, trip, service, scheduled hour and date. Needs the optional
`ml` dependency group."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

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
    """A stable integer per (agency, route), the categorical code the models split
    on. A route's identity is known in advance, so the vocabulary may span the
    whole table without leaking anything."""
    keys = runs[ROUTE].drop_duplicates().sort_values(ROUTE).reset_index(drop=True)
    return keys.assign(route_id=np.arange(len(keys), dtype="int32"))


def with_route_ids(runs: pd.DataFrame, ids: pd.DataFrame) -> pd.DataFrame:
    return runs.merge(ids, on=ROUTE, how="left", validate="many_to_one")


def build_frame(runs: pd.DataFrame, origin: date, *, window_days: int = 28, horizon_days: int = 7) -> pd.DataFrame:
    """One row per run on origin+1..origin+horizon_days, with its features as of
    origin−1 and its actual delay. The origin day itself is neither history nor
    target. `runs` must carry `route_id` (with_route_ids)."""
    t = pd.Timestamp(origin)
    last = t - pd.Timedelta(days=1)
    past = runs[runs["service_date"] <= last]
    hist = past[past["service_date"] >= t - pd.Timedelta(days=window_days)]
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
    if past.empty:
        frame["service_weekend_share"] = np.nan
    else:
        frame = frame.join(_weekend_share(past), on=["agency_id", "service"])

    frame[_COUNTS] = frame[_COUNTS].fillna(0)
    numeric = [column for column in [*FEATURES, "slot_p10", "slot_p90"] if column not in CATEGORICAL]
    frame[numeric] = frame[numeric].astype("float32")
    return frame.reset_index(drop=True)


def _slot_stats(hist: pd.DataFrame) -> pd.DataFrame:
    """B0 (the slot's runs-weighted mean), B1 (its latest day) and the slot's
    spread over the window. p10/p90 are B0's own interval."""
    grouped = hist.groupby(SLOT)["delay_min"]
    stats = grouped.agg(slot_mean="mean", slot_runs="size", slot_std="std")
    stats["slot_days"] = hist.groupby(SLOT)["service_date"].nunique()
    bounds = grouped.quantile([0.1, 0.9]).unstack()
    stats["slot_p10"] = bounds[0.1]
    stats["slot_p90"] = bounds[0.9]
    daily = hist.groupby([*SLOT, "service_date"])["delay_min"].mean().reset_index()
    stats["slot_last"] = daily.sort_values("service_date").groupby(SLOT)["delay_min"].last()
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


def _weekend_share(past: pd.DataFrame) -> pd.Series:
    """The share of the days a service has run that fell on a Saturday or Sunday.
    A weekend service on a weekday is a holiday timetable."""
    days = past[["agency_id", "service", "service_date"]].drop_duplicates()
    weekend = (days["service_date"].dt.dayofweek >= 5).astype("float64")
    return weekend.groupby([days["agency_id"], days["service"]]).mean().rename("service_weekend_share")
