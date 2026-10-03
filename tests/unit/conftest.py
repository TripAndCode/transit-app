"""Conftest for pure-unit tests that have no database dependency.

Overrides the session-scoped ``apply_schema`` and ``_clear_compute_caches``
fixtures from the parent conftest so that tests in this directory do not
attempt to run migrations against any Postgres instance.
"""

from collections.abc import Iterable
from pathlib import Path

import pytest

_UNIT_DIR = Path(__file__).resolve().parent
_CH_FIXTURES = frozenset({"ch_client", "ch_async_client"})


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

    `reset_cache()` rather than `invalidate()`: the latter keeps the entries
    on purpose, and a synchronous reader is served them, so it does not give
    the next test a clean slate.
    """
    from pipeline.flags import reset_cache

    reset_cache()
    yield
    reset_cache()


def clickhouse_gated(fixturenames: Iterable[str], skip_reasons: Iterable[str]) -> bool:
    """Whether a test needs the throwaway ClickHouse: it asks for the client
    fixture, or it skips itself for want of `make ch-test`. Pure, so the rule
    is testable without a collection run."""
    if _CH_FIXTURES & set(fixturenames):
        return True
    return any("ch-test" in reason or "RUN_CH_INTEGRATION" in reason for reason in skip_reasons)


def pytest_collection_modifyitems(session, config, items):
    """A ClickHouse-gated test under tests/unit skips silently without
    RUN_CH_INTEGRATION=1 and reads as a pass. Fail the collection instead so
    it is moved to tests/clickhouse/, where the gate is the point."""
    offenders = []
    for item in items:
        if _UNIT_DIR not in Path(str(item.path)).parents:
            continue
        reasons = [str(mark.kwargs.get("reason", "")) for mark in item.iter_markers(name="skipif")]
        if clickhouse_gated(getattr(item, "fixturenames", ()), reasons):
            offenders.append(item.nodeid)
    if offenders:
        raise pytest.UsageError(
            "tests/unit holds ClickHouse-gated tests; move them to tests/clickhouse/:\n  " + "\n  ".join(offenders)
        )
