"""Display helpers for DB column values shared across the query layer.

Lifted from the now-retired pipeline/query/formatter.py. Leading
underscores dropped because these are imported by tool_queries.py,
tools.py, and potentially future helpers — they are public API of
this module.
"""

from datetime import time as _time

from pipeline.db import _DOW_ISO_TO_JP


def dow_label(value) -> str:
    """Render a DOW column value for display.

    Accepts an ISODOW int (1..7) and returns the matching Japanese char
    ('月'..'日'). Strings pass through unchanged — that path covers
    rollup labels like '平日' / '週末' emitted by the weekday/weekend
    grouping SQL, and tolerates legacy Japanese-char input.
    """
    if isinstance(value, int):
        return _DOW_ISO_TO_JP.get(value, str(value))
    return str(value)


def time_label(value) -> str:
    """Render a scheduled_time value as 'HH:MM'.

    Post migration 0011, TIME columns return datetime.time objects;
    pre-migration paths still passed 'HH:MM' or 'HH:MM:SS' strings.
    Both shapes normalise to 'HH:MM' so display stays stable across
    the migration boundary. None becomes the empty string so the
    formatter doesn't leak the literal 'None'.
    """
    if isinstance(value, _time):
        return value.strftime("%H:%M")
    if isinstance(value, str) and len(value) >= 5 and value[2] == ":":
        return value[:5]
    return str(value) if value is not None else ""
