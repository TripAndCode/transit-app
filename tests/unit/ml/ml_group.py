"""The `ml` Poetry group is optional. A test that needs it skips where the group
is not installed, except where ML_DEPS_REQUIRED=1 (CI's test job): there a
missing group fails the test instead of skipping it."""

from __future__ import annotations

import importlib
import os
from types import ModuleType

import pytest


def require(name: str) -> ModuleType:
    if os.environ.get("ML_DEPS_REQUIRED") == "1":
        return importlib.import_module(name)
    return pytest.importorskip(name)
