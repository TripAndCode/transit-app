"""How long a rider who reaches a stop at a random moment of an hour waits for
the next departure of any route there, as run and as timetabled.

A rider arriving at x waits until the first departure at or after x. Over
arrivals spread evenly across a window, the departures cut the window into
spans; the span ending at departure t from x0 to x1 holds waits spread evenly
from t - x1 to t - x0, weighted by its length x1 - x0. Pooling such spans
over days gives a wait distribution whose CDF is piecewise linear, so its
quantiles are found exactly by sweeping the span ends (wait_quantiles).

A day's hour is judged only when the feed saw at least the planned number
of departures in each hour that could serve it (judged); a missed one would
merge two gaps into one and lengthen the waits. The timetabled side reads the same days' departures at their
scheduled times, so the two differ only by how the buses ran.
"""

from __future__ import annotations

from bisect import bisect_right
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
    x0 = start
    i = bisect_right(departures, start)
    while i < len(departures):
        t = departures[i]
        x1 = min(end, t)
        if x1 > x0:
            spans.append((t - x1, t - x0))
        if t >= end:
            break
        x0 = t
        i += 1
    return spans


def wait_quantiles(spans: Sequence[tuple[int, int]], qs: Sequence[Fraction]) -> list[Fraction]:
    """The ascending quantiles *qs* of the waits *spans* hold, each spread
    evenly over its range and weighted by its length. ValueError with no span."""
    if not spans:
        raise ValueError("no arrival to take a quantile of")
    total = sum(hi - lo for lo, hi in spans)
    # Below a wait w, a span holds min(max(w - lo, 0), hi - lo) of its arrivals:
    # the CDF gains slope 1 at each lo and loses it at each hi. The sweep stays in
    # integers, setting the area against each q's numerator and denominator.
    ends = sorted([(lo, 1) for lo, _ in spans] + [(hi, -1) for _, hi in spans])
    pending = list(qs)
    found: list[Fraction] = []
    area = slope = 0
    w = ends[0][0]
    for at, step in ends:
        reach = area + slope * (at - w)
        while pending and slope and reach * pending[0].denominator >= pending[0].numerator * total:
            found.append(w + (pending.pop(0) * total - area) / slope)
        if not pending:
            return found
        area, w, slope = reach, at, slope + step
    return found + [Fraction(w)] * len(pending)


def serving(planned: Mapping[int, int], hour: int) -> tuple[int, ...]:
    """The hours whose departures can serve riders arriving in *hour*: the
    hour itself, the hour before (a late bus runs into it) and the next hour
    the *planned* departures per hour have one in."""
    following = min((h for h, n in planned.items() if h > hour and n > 0), default=None)
    return (hour - 1, hour) if following is None else (hour - 1, hour, following)


def judged(observed: Mapping[int, int], planned: Mapping[int, int], hours: Iterable[int]) -> bool:
    """Whether a day whose departures were *observed* per hour saw at least
    the *planned* ones in each of *hours*, those serving an hour's riders."""
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
    """The waits of riders arriving in each of *hours*, over *days* given as
    (scheduled_secs, dep_delays), the departures one stop saw on one service
    day at service-day seconds, set against the typical day's *planned*
    departures per hour. A day's riders are the same on both sides: those
    arriving before its earlier last departure, as run or as timetabled. A
    day counts for an hour when it is judged for it and has riders in it; an
    hour no day counts for is left out."""
    hours = list(hours)
    needed = {hour: serving(planned, hour) for hour in hours}
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
            if end <= start or not judged(observed, planned, needed[hour]):
                continue
            run[hour] += wait_spans(actual, start, end)
            timetabled[hour] += wait_spans(scheduled, start, end)
            counted[hour] += 1
    # A counted day's window ends at or before its last departure on both
    # sides, so each counted hour has spans on both.
    return {
        hour: HourWait(n, *wait_quantiles(run[hour], (P50, P90)), *wait_quantiles(timetabled[hour], (P50, P90)))
        for hour, n in counted.items()
    }
