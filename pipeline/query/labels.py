"""Display helpers for DB column values shared across the query layer.

``dow_label`` and ``time_label`` were lifted out of
``pipeline/query/formatter.py`` so the LLM tool surface
(``pipeline.query.tools``) can render rows without depending on the
report-layer formatter module. The leading underscore from the
formatter-internal names was dropped because these are now public API
of this module.

DOW rendering now honours a ``lang`` parameter so the Ask tab and the
reports text body can speak the user's UI locale. Rollup labels
(``平日`` / ``週末``) are translated as well so the formatter doesn't
have to special-case them at the call site.
"""

from datetime import time as _time

from pipeline.db import _DOW_ISO_TO_JP

# ISODOW (Mon=1..Sun=7) → short English label, matching the JP char width
# in display tables ('Mon' / 'Tue' / …).
_DOW_ISO_TO_EN = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}

# Japanese day-group words → EN: the rollup labels the weekday/weekend
# grouping SQL emits, and the service filter's values (api.range.ServiceType).
# The DOW values dow_label renders are ISODOW ints or the 平日/週末 rollups,
# never 土曜 or 日祝, so the service values change no DOW rendering.
_DAY_GROUP_JP_TO_EN = {
    "平日": "Weekday",
    "週末": "Weekend",
    "土曜": "Saturday",
    "日祝": "Sunday/Holiday",
    "土日祝": "Weekend/Holiday",
}


def day_group_label(value: str, lang: str = "ja") -> str:
    """Render a Japanese day-group word (a DOW rollup or a service-filter
    value) in ``lang``; a word with no English entry passes through."""
    return _DAY_GROUP_JP_TO_EN.get(value, value) if lang == "en" else value


def dow_label(value, lang: str = "ja") -> str:
    """Render a DOW column value for display.

    Accepts an ISODOW int (1..7) and returns the matching locale-appropriate
    label ('月'..'日' for ``ja``, 'Mon'..'Sun' for ``en``). Strings pass
    through after a rollup-label translation pass — that path covers
    '平日'/'週末' (and tolerates legacy Japanese-char input).
    """
    en = lang == "en"
    if isinstance(value, int):
        if en:
            return _DOW_ISO_TO_EN.get(value, str(value))
        return _DOW_ISO_TO_JP.get(value, str(value))
    return day_group_label(str(value), lang)


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
