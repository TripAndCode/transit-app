"""A route page snapshot stores the agency's route lists at the length the
live page asks for, since the strip reads a list shorter than the client's
row limit as complete (``routeStanding`` in
``frontend/src/components/analysis/routeDossier.ts``)."""

import re
from pathlib import Path

from api.routers.snapshots import _ROUTE_PAGE_LIST_LIMIT


def test_the_stored_lists_are_as_long_as_the_live_page_asks():
    hooks = (Path(__file__).parents[2] / "frontend" / "src" / "api" / "hooks.ts").read_text()
    match = re.search(r"export const REPORT_ROWS_MAX = (\d+);", hooks)
    assert match is not None
    assert int(match.group(1)) == _ROUTE_PAGE_LIST_LIMIT
