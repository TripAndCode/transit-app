"""Expected-delay lookup: summarize agg_route_hour into a typical-delay figure.

Pure (no DB) so the hour-bucketing, sample-weighting, and disclaimer-case logic
are unit-testable. The endpoint (api/routers/reports.py) supplies the rows + locale.
This is a seasonal-naive baseline ("expected delay"), NOT a prediction — see
DELAY_ANALYSIS.md.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from api.triage import LOW_CONFIDENCE_SAMPLES

# Plain-language disclaimers — NO jargon (p90/percentile/baseline forbidden). Each
# template states the actual basis (which slot, how many measurements) and what it
# excludes, and defines a "measurement" inline so `samples` is never undefined.
_DISCLAIMER: dict[tuple[str, str], str] = {
    ("normal", "ja"): (
        "{service_type}の{hour}時台に走ったこの路線の遅れの計測{samples}回分から計算した平均です"
        "（1回＝ある日のある停留所での1計測）。事故・天候・当日の運行状況は反映していないため、"
        "実際の遅れは変わることがあります。"
    ),
    ("normal", "en"): (
        "The average of {samples} past delay measurements for this route's {service_type} "
        "departures in the {hour}:00 hour (each measurement = one stop, on one run, on one day). "
        "It does not account for incidents, weather, or today's conditions, so the actual delay can differ."
    ),
    ("low", "ja"): (
        "計測が{samples}回と少ないため、あくまで参考値です。{service_type}の{hour}時台のこの路線の"
        "過去の遅れの計測から計算しましたが、事故・天候・当日の状況は反映していません。"
    ),
    ("low", "en"): (
        "Based on only {samples} past delay measurements, so treat this as a rough indication. "
        "Computed from this route's {service_type} departures in the {hour}:00 hour; it does not "
        "reflect incidents, weather, or today's conditions."
    ),
    ("none", "ja"): "{service_type}の{hour}時台のこの路線の計測記録がないため、目安を出せません。",
    ("none", "en"): (
        "There are no past delay measurements for this route's {service_type} departures in the "
        "{hour}:00 hour, so no estimate is available."
    ),
}


def _disclaimer(case: str, locale: str, **vars: Any) -> str:
    lang = locale if locale in ("ja", "en") else "ja"
    tpl = _DISCLAIMER.get((case, lang)) or _DISCLAIMER[(case, "ja")]
    return tpl.format(**vars)


def _hour_of(scheduled_time: Any) -> "int | None":
    """Parse the hour from a scheduled_time text like '17:37:00'. None if unparseable."""
    try:
        return int(str(scheduled_time).split(":")[0])
    except (ValueError, IndexError, AttributeError):
        return None


def summarize_expected_delay(
    rows: Iterable[Mapping[str, Any]],
    route: str,
    service_type: str,
    hour: int,
    locale: str = "ja",
) -> dict:
    """Summarize agg_route_hour rows into the expected-delay payload. Pure.

    `rows`: mappings with keys scheduled_time, avg_min, p90_min, samples
    (asyncpg Records or plain dicts). Keeps only rows in `hour` with non-null
    metrics, sample-weights avg/p90, and attaches a 3-case disclaimer.
    """
    matched = [
        r for r in rows
        if _hour_of(r["scheduled_time"]) == hour
        and r["avg_min"] is not None
        and r["p90_min"] is not None
        and r["samples"]
    ]
    total = sum(r["samples"] for r in matched)
    base = {"route": route, "service_type": service_type, "hour": hour, "samples": total}

    if total == 0:
        return {
            **base,
            "expected_avg_min": None,
            "expected_p90_min": None,
            "low_confidence": True,
            "disclaimer": _disclaimer("none", locale, service_type=service_type, hour=hour),
        }

    avg = round(sum(r["avg_min"] * r["samples"] for r in matched) / total, 1)
    p90 = round(sum(r["p90_min"] * r["samples"] for r in matched) / total, 1)
    low = total < LOW_CONFIDENCE_SAMPLES
    return {
        **base,
        "expected_avg_min": avg,
        "expected_p90_min": p90,
        "low_confidence": low,
        "disclaimer": _disclaimer(
            "low" if low else "normal", locale, service_type=service_type, hour=hour, samples=total
        ),
    }
