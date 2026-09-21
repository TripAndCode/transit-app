"""Query-shape guards for `pipeline.dashboard_queries`' route-label lookup.

The lookup must narrow to the routes actually needed rather than reading an
agency's whole `static_routes` table. Both `movers` and `_build_heatmap` need
that, and they used to carry separate copies of the SQL — so these assert on
the shared definition *and* that each caller still routes through it, since a
caller quietly reintroducing its own unfiltered copy is the way this
regresses. Source-text inspection; no DB needed.
"""

from __future__ import annotations

import inspect

from pipeline import dashboard_queries as dq


def test_route_label_lookup_is_filtered_to_the_requested_routes():
    assert "= ANY($2::text[])" in dq._ROUTE_LABEL_SQL


def test_both_callers_use_the_shared_lookup():
    for fn in (dq.movers, dq._build_heatmap):
        src = inspect.getsource(fn)
        assert "_route_labels(" in src, f"{fn.__name__} no longer uses the shared route-label lookup"
        assert "FROM static_routes" not in src, f"{fn.__name__} has its own copy of the label query again"
