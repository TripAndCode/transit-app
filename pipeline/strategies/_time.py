"""GTFS departure_time normalization, shared by every ingest strategy.

departure_time is free text ("7:05:00", "07:05", "25:30:00" for a trip
continuing past midnight, or "" for a non-timepoint stop) straight from an
agency's own static feed -- not validated anywhere upstream
(pipeline/static_loader.py loads it verbatim), so it must never raise.
"""

import re
from typing import Literal

# ONE parse: the extended-hour (>=24) decision and the zero-pad must never
# disagree, which is what happened when static_join.py checked sched[:2] for
# extended-hour and hour_str.isdigit() for padding separately -- a 3+ digit
# hour token (e.g. "125:30:00") had a 2-char prefix under 24 and passed the
# isdigit() pad unchanged, storing an unparseable value. [0-9], not \d:
# str.isdigit()/\d both accept non-ASCII decimal digits (Unicode category Nd,
# e.g. Arabic-Indic "٣") that int() can raise ValueError on for some code
# points (e.g. superscript "²", category No, isdigit() True but int() raises).
_GTFS_TIME_RE = re.compile(r"^([0-9]{1,2}):([0-5][0-9])(?::([0-5][0-9]))?$")

Status = Literal["ok", "empty", "extended", "bad"]


def parse_departure_time(raw: str | None) -> tuple[str | None, Status, int | None]:
    """Normalize a GTFS departure_time and also return its raw
    seconds-since-service-day-start value.

    Returns ``(normalized, status, scheduled_sec)``. ``normalized`` /
    ``status`` follow ``normalize_departure_time``'s contract exactly (see
    below). ``scheduled_sec`` is populated for both ``"ok"`` and
    ``"extended"`` -- GTFS's post-midnight-continuation hours (e.g.
    "25:30:00") are well-defined arithmetic (25*3600 + 30*60 = 91800) even
    though there's no same-day "HH:MM[:SS]" string that can hold them --
    and is ``None`` only for ``"empty"``/``"bad"`` input that didn't parse
    as a time at all.

    This is the ONE regex match (see module docstring): every caller that
    needs the formatted string, the extended-hour decision, and/or the raw
    seconds value must go through this single parse, not repeat it, so the
    three can never disagree with each other.
    """
    if not raw or not raw.strip():
        return None, "empty", None
    m = _GTFS_TIME_RE.match(raw.strip())
    if m is None:
        return None, "bad", None
    hh, mm, ss = m.groups()
    hour = int(hh)
    scheduled_sec = hour * 3600 + int(mm) * 60 + (int(ss) if ss else 0)
    if hour >= 24:
        return None, "extended", scheduled_sec
    return (f"{hour:02d}:{mm}:{ss}" if ss else f"{hour:02d}:{mm}"), "ok", scheduled_sec


def normalize_departure_time(raw: str | None) -> tuple[str | None, Status]:
    """Normalize a GTFS departure_time to zero-padded "HH:MM[:SS]".

    Never raises. Returns ``(None, "empty")`` for an empty/missing value
    (legal GTFS for a non-timepoint stop), ``(None, "extended")`` for a
    valid but un-representable extended hour (>=24, a trip continuing past
    midnight), ``(None, "bad")`` for anything else that doesn't parse as
    "H:MM[:SS]", and ``(normalized, "ok")`` otherwise. The caller decides
    what to do with each status; this function only classifies.

    Thin wrapper around ``parse_departure_time`` that drops the raw
    ``scheduled_sec`` value -- callers that need seconds-since-service-day-
    start too (e.g. static_join.py, which stores extended-hour trips
    instead of dropping them) should call ``parse_departure_time`` directly
    rather than re-deriving seconds from this function's already-lossy
    (NULL for "extended") output.
    """
    sched, status, _ = parse_departure_time(raw)
    return sched, status
