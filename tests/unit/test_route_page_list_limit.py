"""The server's report row limit is the client's: a route page snapshot
stores the agency's route lists at the length the live page asks for, and
the strip reads a list shorter than the client's limit as complete
(``routeStanding`` in ``frontend/src/components/analysis/routeDossier.ts``)."""

import re
from pathlib import Path

from api.routers.reports import REPORT_ROWS_MAX


def test_the_servers_row_limit_is_the_clients():
    hooks = (Path(__file__).parents[2] / "frontend" / "src" / "api" / "hooks.ts").read_text()
    match = re.search(r"export const REPORT_ROWS_MAX = (\d+);", hooks)
    assert match is not None
    assert int(match.group(1)) == REPORT_ROWS_MAX
