"""The JST anchor the query-tool fixtures seed from.

Those fixtures insert a row per minute and then assert per-civil-day
groupings. The queries bucket by ``api.range.jst_today``, so an anchor taken
from the wall clock straddles JST midnight whenever CI runs in the hour
before 15:00 UTC — six trips land as five and one, and the assertion fails
for a reason unrelated to the diff under test.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from api.range import jst_today
from tests.query.test_tool_queries import jst_midday

JST = timezone(timedelta(hours=9))


def test_anchor_is_inside_todays_jst_day():
    assert jst_midday().astimezone(JST).date() == jst_today()


def test_a_fixtures_worth_of_offsets_cannot_cross_the_boundary():
    """The fixtures add minutes, not hours. Midday leaves twelve hours of
    headroom either way; this fails the moment the anchor drifts toward an
    edge, which is what made the wall-clock version fragile."""
    anchor = jst_midday()
    for minutes in (-120, -60, -1, 0, 1, 60, 120):
        shifted = (anchor + timedelta(minutes=minutes)).astimezone(JST).date()
        assert shifted == jst_today(), f"{minutes:+} min crosses the JST day boundary"
