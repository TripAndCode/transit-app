"""Plain data carried from build_digest() to render_digest() — no logic."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Mover:
    route_code: str
    avg_delay_min: float
    baseline_avg_min: float | None
    deviation_min: float
    bucket: str  # "anomaly" | "watch"
    low_confidence: bool


@dataclass(frozen=True)
class AgencySection:
    agency_id: int
    agency_name: str
    has_data: bool
    avg_delay_min: float | None
    baseline_avg_min: float | None
    delta_min: float | None
    movers: list[Mover]
    raw_samples: int
    clamp_count: int
    is_stale: bool


@dataclass(frozen=True)
class DigestData:
    target_day: date
    network_avg_delay_min: float | None
    sections: list[AgencySection]
    # False iff the ClickHouse freshness probe backing every section's
    # `is_stale` failed (see build_digest) — distinct from "probe ran, found
    # nothing stale" (every section's is_stale is False either way, so this
    # flag is the only signal render_digest has to tell "known fresh" apart
    # from "staleness unknown"). Defaults True so existing callers/tests that
    # construct DigestData directly (with a real or no-op freshness check)
    # don't need to know about this failure mode.
    staleness_known: bool = True
