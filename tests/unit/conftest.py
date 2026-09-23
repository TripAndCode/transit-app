"""Conftest for pure-unit tests that have no database dependency.

Overrides the session-scoped ``apply_schema`` and ``_clear_compute_caches``
fixtures from the parent conftest so that tests in this directory do not
attempt to run migrations against any Postgres instance.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    """No-op: pure-unit tests need no DB schema."""


@pytest.fixture(autouse=True)
def _clear_compute_caches():
    """Reset `pipeline.flags`'s TTL cache between tests.

    Unlike the report `compute_*` caches this fixture is named for, `flags`
    has to be reset even here: its 30s TTL would otherwise leak a value
    resolved (or an env fallback taken) by one test into the next, since
    pytest runs the whole unit suite well inside that window.
    """
    from pipeline.flags import invalidate

    invalidate()
    yield
    invalidate()
