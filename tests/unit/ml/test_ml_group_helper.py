"""Pins ml_group.require()'s branching on its own, independent of whether any
optional package happens to be installed. A plain test_ml_group.py can't do
this: its module-level require() calls would run before any monkeypatch."""

import pytest

from tests.unit.ml.ml_group import require


def test_require_raises_when_the_flag_is_set_and_the_module_is_missing(monkeypatch):
    # Not `pytest.raises(ModuleNotFoundError)`: pytest.skip.Exception isn't a
    # ModuleNotFoundError, so `raises` would let it propagate uncaught rather
    # than fail the assertion -- and pytest marks an uncaught skip SKIPPED,
    # not FAILED, which would hide the exact regression this test exists for.
    monkeypatch.setenv("ML_DEPS_REQUIRED", "1")
    try:
        require("not_a_real_module_xyz")
    except pytest.skip.Exception:
        pytest.fail("require() skipped despite ML_DEPS_REQUIRED=1")
    except ModuleNotFoundError:
        pass
    else:
        pytest.fail("require() did not raise for a missing module")


def test_require_skips_when_the_flag_is_unset_and_the_module_is_missing(monkeypatch):
    monkeypatch.delenv("ML_DEPS_REQUIRED", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require("not_a_real_module_xyz")
