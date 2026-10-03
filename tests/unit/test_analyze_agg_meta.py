"""`agg_meta.analyzed_at` has to be the moment the build finished.

The control board turns that timestamp into a JST calendar day and calls an
agency "fresh" only when it is later than the day being drawn. `now()` is the
transaction's start, and analyze's aggregate build runs inside one
transaction that can span minutes to hours — long enough to land on the
previous civil day and report a completed run as stale. Asserted against the
statement text because the difference is invisible in any result the
function returns.
"""

from __future__ import annotations

import inspect

from pipeline import analyze as analyze_module


def _agg_meta_statement() -> str:
    source = inspect.getsource(analyze_module)
    start = source.index("INSERT INTO agg_meta")
    return " ".join(source[start : source.index("ON CONFLICT (agency_id)", start)].split())


def test_analyzed_at_is_stamped_at_completion_not_at_transaction_start():
    statement = _agg_meta_statement()
    assert "clock_timestamp()" in statement
    assert "now()" not in statement
