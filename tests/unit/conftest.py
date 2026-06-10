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
    """No-op: pure-unit tests have no module-level caches to clear."""
