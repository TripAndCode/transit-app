"""Pure query-shape test for `pipeline.dashboard_queries.movers` (E4): the
route-label lookup must be filtered to the routes actually needed, matching
the sibling filter in `_build_heatmap` (~line 93). No DB needed -- inspects
the function's own source text.
"""

from __future__ import annotations

import inspect

from pipeline.dashboard_queries import movers


def test_movers_label_lookup_is_filtered_to_requested_routes():
    src = inspect.getsource(movers)
    assert "= ANY($2::text[])" in src
