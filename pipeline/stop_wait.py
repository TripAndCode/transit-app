"""How long a rider who reaches a stop at a random moment of an hour waits for
the next departure of any route there, as run and as timetabled.

A rider arriving at x waits until the first departure at or after x. Over
arrivals spread evenly across a window, the departures cut the window into
spans; the span ending at departure t from x0 to x1 holds waits spread evenly
from t - x1 to t - x0, weighted by its length x1 - x0. Pooling such spans
over days gives a wait distribution whose CDF is piecewise linear, so its
quantiles are found exactly by sweeping the span ends (wait_quantile).

A day's hour is judged only when the feed saw every departure that could
serve it (judged); a missed one would merge two gaps into one and lengthen
the waits. The timetabled side reads the same days' departures at their
scheduled times, so the two differ only by how the buses ran.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from typing import NamedTuple

HOUR_SEC = 3600
P50 = Fraction(1, 2)
P90 = Fraction(9, 10)


def wait_spans(departures: Sequence[int], start: int, end: int) -> list[tuple[int, int]]:
    """The (shortest, longest) wait of each span of arrivals in [*start*,
    *end*) that the sorted *departures* serve. An arrival after the last
    departure has none to wait for and falls in no span."""
    spans = []
    previous = None
    for t in departures:
        x0 = start if previous is None else max(start, previous)
        x1 = min(end, t)
        if x1 > x0:
            spans.append((t - x1, t - x0))
        if t >= end:
            break
        previous = t
    return spans


def wait_quantile(spans: Sequence[tuple[int, int]], q: Fraction) -> Fraction:
    """The *q* quantile of the waits *spans* hold, each spread evenly over its
    range and weighted by its length. ValueError with no span."""
    if not spans:
        raise ValueError("no arrival to take a quantile of")
    target = q * sum(hi - lo for lo, hi in spans)
    # Below a wait w, a span holds min(max(w - lo, 0), hi - lo) of its arrivals:
    # the CDF gains slope 1 at each lo and loses it at each hi.
    ends = sorted([(lo, 1) for lo, _ in spans] + [(hi, -1) for _, hi in spans])
    area: Fraction | int = 0
    slope = 0
    w = ends[0][0]
    for at, step in ends:
        if slope and area + slope * (at - w) >= target:
            return w + (target - area) / slope
        area += slope * (at - w)
        w = at
        slope += step
    return Fraction(w)


def judged(observed: Mapping[int, int], planned: Mapping[int, int], hour: int) -> bool:
    """Whether a day whose departures were *observed* per hour is judged for
    *hour*: every hour whose departures can serve its riders has at least
    the *planned* ones, namely the hour itself, the hour before (a late bus
    runs into it) and the next hour the timetable has a departure in."""
    following = min((h for h, n in planned.items() if h > hour and n > 0), default=None)
    hours = {hour - 1, hour} if following is None else {hour - 1, hour, following}
    return all(observed.get(h, 0) >= planned.get(h, 0) for h in hours)


class HourWait(NamedTuple):
    """One hour's waits in seconds pooled over its judged days: as run, and
    as the same departures were timetabled."""

    days: int
    p50_sec: Fraction
    p90_sec: Fraction
    scheduled_p50_sec: Fraction
    scheduled_p90_sec: Fraction


def hour_waits(
    days: Iterable[tuple[Sequence[int], Sequence[int]]], planned: Mapping[int, int], hours: Iterable[int]
) -> dict[int, HourWait]:
    """Each of *hours* with a judged day: the waits of riders arriving in it,
    over *days* given as (scheduled_secs, dep_delays), the departures one
    stop saw on one service day at service-day seconds, set against the
    typical day's *planned* departures per hour. A day's riders are the same
    on both sides: those arriving before its earlier last departure, as run
    or as timetabled."""
    hours = list(hours)
    run: dict[int, list[tuple[int, int]]] = {h: [] for h in hours}
    timetabled: dict[int, list[tuple[int, int]]] = {h: [] for h in hours}
    counted: Counter[int] = Counter()
    for scheduled_secs, dep_delays in days:
        if not scheduled_secs:
            continue
        observed = Counter(sec // HOUR_SEC for sec in scheduled_secs)
        actual = sorted(sec + delay for sec, delay in zip(scheduled_secs, dep_delays, strict=True))
        scheduled = sorted(scheduled_secs)
        last = min(actual[-1], scheduled[-1])
        for hour in hours:
            start, end = hour * HOUR_SEC, min((hour + 1) * HOUR_SEC, last)
            if end <= start or not judged(observed, planned, hour):
                continue
            run[hour] += wait_spans(actual, start, end)
            timetabled[hour] += wait_spans(scheduled, start, end)
            counted[hour] += 1
    # A counted day's window ends at or before its last departure on both
    # sides, so each counted hour has spans on both.
    return {
        hour: HourWait(
            n,
            wait_quantile(run[hour], P50),
            wait_quantile(run[hour], P90),
            wait_quantile(timetabled[hour], P50),
            wait_quantile(timetabled[hour], P90),
        )
        for hour, n in counted.items()
    }
