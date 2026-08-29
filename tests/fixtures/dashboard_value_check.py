"""Pure helpers for comparing a scraped dashboard DOM string against item 21's
hand-computed ground truth (item 22 — "does the frontend actually render
`agg_*` numbers correctly, not just does the pipeline compute them
correctly").

Split out from `tests/dashboard_synthetic_display_test.py` (which needs a
real browser + the throwaway Postgres/ClickHouse stack and is skipped by
default — see that module's docstring) so the comparison LOGIC itself can be
exercised by fast, offline, always-run tests too
(`tests/unit/test_dashboard_value_check.py`), mirroring item 23's
`tests/ask_eval/numeric_ground_truth.py` split: prove the check isn't
vacuous without needing a browser/DB at all.

Every dashboard surface this repo's ``ReportTable.tsx``/``OverviewHeroRow.tsx``
render numbers through JS ``toFixed(1)`` (see ``fmtMin`` in
``frontend/src/components/ReportTable.tsx`` and the inline
``headline.avg_min.toFixed(1)`` in
``frontend/src/components/OverviewHeroRow.tsx``), one more rounding step past
item 21's 2-decimal-place `agg_route_stats` values — so comparisons here
round the *expected* side to 1dp before comparing, rather than comparing at
2dp and risking a spurious failure on the display layer's own (correct)
rounding.
"""

from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def extract_leading_number(text: str) -> float:
    """Parse the first decimal number out of a rendered dashboard cell.

    Handles the unit suffixes/locale text the frontend appends around the
    number (``"0.9分"``, ``"0.9 min"``) and thousands-grouping from JS
    ``toLocaleString()`` (``"1,234"``) by stripping commas first and matching
    only the numeric run, not the whole cell string.
    """
    cleaned = text.replace(",", "")
    m = _NUMBER_RE.search(cleaned)
    if not m:
        raise ValueError(f"no numeric value found in {text!r}")
    return float(m.group(0))


def assert_avg_min_matches(cell_text: str, expected_avg_min: float, *, label: str) -> None:
    """Compare a scraped avg-delay cell against item 21's raw (2dp)
    ``agg_route_stats``/``agg_route_daily``-derived ``avg_min`` expectation.

    Rounds the expected side to 1dp (half-up, matching JS ``toFixed``'s
    behaviour for the clean, non-boundary values item 21's patterns use)
    before comparing, since every on-screen surface this test checks
    re-rounds to 1dp for display.
    """
    actual = extract_leading_number(cell_text)
    expected_rounded = round(expected_avg_min, 1)
    if actual != expected_rounded:
        raise AssertionError(
            f"{label}: displayed avg delay {actual} does not match item 21's expected "
            f"{expected_avg_min} (rounded to {expected_rounded} for display) — cell text was {cell_text!r}"
        )


def assert_samples_matches(cell_text: str, expected_samples: int, *, label: str) -> None:
    """Compare a scraped sample-count cell against item 21's expected count."""
    actual = extract_leading_number(cell_text)
    if actual != expected_samples:
        raise AssertionError(
            f"{label}: displayed sample count {actual} does not match item 21's expected "
            f"{expected_samples} — cell text was {cell_text!r}"
        )
