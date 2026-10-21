"""The JST anchor that date-sensitive fixtures seed from.

The query-tool fixtures insert a row per minute and then assert per-day groupings.
The queries bucket by ``api.range.jst_today``, or pair a trip's stops within
one service date, so an anchor taken from the wall clock straddles JST
midnight, or the 04:00 JST service-day cutover, whenever CI runs just before
either. Six trips land as five and one, and the assertion fails for a reason
unrelated to the diff under test.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from api.range import jst_today
from pipeline.service_day import service_date_of
from tests.fixtures.clock import jst_midday

JST = timezone(timedelta(hours=9))


def test_anchor_is_inside_todays_jst_day():
    assert jst_midday().astimezone(JST).date() == jst_today()


def test_a_fixtures_worth_of_offsets_cannot_cross_the_boundary():
    """The fixtures add minutes, not hours. Midday leaves twelve hours of
    headroom to JST midnight either way and eight back to the service-day
    cutover; this fails the moment the anchor drifts toward an edge, which is
    what made the wall-clock version fragile."""
    anchor = jst_midday()
    for minutes in (-120, -60, -1, 0, 1, 60, 120):
        shifted = anchor + timedelta(minutes=minutes)
        assert shifted.astimezone(JST).date() == jst_today(), f"{minutes:+} min crosses the JST day boundary"
        assert service_date_of(shifted) == service_date_of(anchor), f"{minutes:+} min crosses the service-day cutover"
