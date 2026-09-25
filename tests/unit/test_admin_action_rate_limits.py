"""Admin mutation endpoints that queue real pipeline/feed work must sit
behind a rate limit, the same as every other mutating route (`auth.py`,
`map.py`).

`slowapi`'s `@limiter.limit(...)` wraps the endpoint with `functools.wraps`,
so the decorated function carries `__wrapped__` pointing at the original --
the same attribute `tests/unit/test_forecast_overview_logging.py` uses to
reach past an existing limiter to call the raw handler. Its presence here is
a structural proxy for "this route is rate-limited" that needs no real
`Request` or storage backend.
"""

import api.routers.admin as admin_mod
import api.routers.admin_agencies as admin_agencies_mod


def test_trigger_run_is_rate_limited():
    assert hasattr(admin_mod.trigger_run, "__wrapped__")


def test_probe_agency_feed_is_rate_limited():
    assert hasattr(admin_agencies_mod.probe_agency_feed, "__wrapped__")


def test_reanalyze_agency_is_rate_limited():
    assert hasattr(admin_agencies_mod.reanalyze_agency, "__wrapped__")
