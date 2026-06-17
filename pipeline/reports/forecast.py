"""Expected-delay lookup: summarize agg_route_hour into a typical-delay figure.

Pure (no DB) so the sample-weighting and disclaimer-case logic are unit-testable.
The endpoint (api/routers/reports.py) does the SQL — including the hour filter via
``EXTRACT(HOUR FROM scheduled_time)`` — and passes the already-filtered rows here.

This is a seasonal-naive baseline ("expected delay"), NOT a prediction — see
DELAY_ANALYSIS.md. Only the sample-weighted mean is reported: a weighted mean of
per-departure averages equals the pooled mean, so it is exact. (A p90 is deliberately
NOT reported — a weighted mean of per-departure p90s is not the pooled-hour p90, and
percentiles cannot be recovered from per-bucket percentiles.)
"""

from collections.abc import Iterable, Mapping
from typing import Any

from api.triage import LOW_CONFIDENCE_SAMPLES

# Plain-language disclaimers — NO jargon (p90/percentile/baseline forbidden). Each
# template states the actual basis (which slot, how many measurements) and what it
# excludes, and defines a "measurement" inline so `samples` is never undefined.
# NOTE: `samples` counts stop-level delay measurements, not distinct days, so the
# LOW_CONFIDENCE_SAMPLES=30 bar is intentionally generous for a pooled hourly slot.
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
    # Guard like pipeline/query/tools.py::_summary: a template/var mismatch returns
    # the raw template rather than raising a 500 in user-facing output.
    try:
        return tpl.format(**vars)
    except (KeyError, IndexError):
        return tpl


def summarize_expected_delay(
    rows: Iterable[Mapping[str, Any]],
    route: str,
    service_type: str,
    hour: int,
    locale: str = "ja",
) -> dict[str, Any]:
    """Summarize already-hour-filtered agg_route_hour rows into the payload. Pure.

    `rows`: mappings with keys avg_min, samples (asyncpg Records or plain dicts),
    pre-filtered by the endpoint to the requested hour. Skips rows with a null
    avg_min or zero/None samples, sample-weights the mean, and attaches a 3-case
    disclaimer (normal / low-confidence / no-data).
    """
    valid = [r for r in rows if r["avg_min"] is not None and r["samples"]]
    total = sum(r["samples"] for r in valid)
    base = {"route": route, "service_type": service_type, "hour": hour, "samples": total}

    if total == 0:
        return {
            **base,
            "expected_avg_min": None,
            "low_confidence": True,
            "disclaimer": _disclaimer("none", locale, service_type=service_type, hour=hour),
        }

    avg = round(sum(r["avg_min"] * r["samples"] for r in valid) / total, 1)
    low = total < LOW_CONFIDENCE_SAMPLES
    return {
        **base,
        "expected_avg_min": avg,
        "low_confidence": low,
        "disclaimer": _disclaimer(
            "low" if low else "normal", locale, service_type=service_type, hour=hour, samples=total
        ),
    }
