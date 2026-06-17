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
