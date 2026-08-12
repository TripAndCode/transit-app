"""DB-free coverage for pipeline.strategies._time.normalize_departure_time.

Complements the Postgres-fixture tests in tests/pipeline/test_static_join.py
(which exercise the full parse_feed path) with the full input space this
function must never raise on -- departure_time is unvalidated free text
straight from an agency's own static feed.
"""

import pytest

from pipeline.strategies._time import normalize_departure_time


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, (None, "empty")),
        ("", (None, "empty")),
        ("   ", (None, "empty")),
        ("7:05:00", ("07:05:00", "ok")),
        ("07:05:00", ("07:05:00", "ok")),
        ("7:05", ("07:05", "ok")),
        ("23:59:59", ("23:59:59", "ok")),
        ("00:00:00", ("00:00:00", "ok")),
        ("25:30:00", (None, "extended")),
        ("24:00:00", (None, "extended")),
        ("99:00:00", (None, "extended")),
        ("ab:05:00", (None, "bad")),
        ("07:99:00", (None, "bad")),
        ("07:05:99", (None, "bad")),
        # 3-digit hour: a fine 2-char prefix ("12") under the old sched[:2]
        # check, but not a valid 1-2 digit hour -- the exact split-brain bug
        # this function exists to close.
        ("125:30:00", (None, "bad")),
        ("07:05:00:00", (None, "bad")),
        ("07:5:00", (None, "bad")),  # minute must be 2 digits
        ("07:05:00.5", (None, "bad")),
        ("07:", (None, "bad")),
        # str.isdigit()/re \d both accept non-ASCII decimal digits that
        # int() can still choke on for some code points -- [0-9] must
        # reject them outright, not depend on int()'s behavior.
        ("²:05:00", (None, "bad")),
        ("٣:05:00", (None, "bad")),  # Arabic-Indic digit -- int()-able, but not ASCII
    ],
)
def test_normalize_departure_time(raw, expected):
    assert normalize_departure_time(raw) == expected


def test_normalize_departure_time_never_raises_on_pathological_input():
    """A 10k-character string must classify as "bad", not hang or raise --
    guards against the regex itself becoming a resource-exhaustion vector
    (departure_time is attacker-adjacent: an agency operator controls their
    own static feed)."""
    normalize_departure_time("9" * 10_000)
    normalize_departure_time(":" * 10_000)
