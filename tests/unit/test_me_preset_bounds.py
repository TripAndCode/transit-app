"""``api.routers.me.PresetIn`` (B10): ``name`` is capped at 120 chars, and
``range_ctx`` (an arbitrary client-supplied dict persisted as jsonb) is
rejected once its JSON serialization exceeds 4096 bytes -- otherwise a
caller could stash unbounded data in a "filter preset".
"""

import pytest
from pydantic import ValidationError

from api.routers.me import PresetIn


def test_preset_in_accepts_name_at_max_length():
    PresetIn(agency_id=1, name="a" * 120, range_ctx={})


def test_preset_in_rejects_name_over_max_length():
    with pytest.raises(ValidationError):
        PresetIn(agency_id=1, name="a" * 121, range_ctx={})


def test_preset_in_accepts_small_range_ctx():
    PresetIn(agency_id=1, name="preset", range_ctx={"from": "2026-01-01", "to": "2026-01-31"})


def test_preset_in_rejects_oversized_range_ctx():
    with pytest.raises(ValidationError):
        PresetIn(agency_id=1, name="preset", range_ctx={"routes": ["x" * 100 for _ in range(200)]})
