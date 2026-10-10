"""Pins ml_group.require()'s branching on its own, independent of whether any
optional package happens to be installed. A plain test_ml_group.py can't do
this: its module-level require() calls would run before any monkeypatch."""

import pytest

from tests.unit.ml.ml_group import require


def test_require_raises_when_the_flag_is_set_and_the_module_is_missing(monkeypatch):
    monkeypatch.setenv("ML_DEPS_REQUIRED", "1")
    with pytest.raises(ModuleNotFoundError):
        require("not_a_real_module_xyz")


def test_require_skips_when_the_flag_is_unset_and_the_module_is_missing(monkeypatch):
    monkeypatch.delenv("ML_DEPS_REQUIRED", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require("not_a_real_module_xyz")
