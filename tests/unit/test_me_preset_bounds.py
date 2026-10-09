"""``api.routers.me.PresetIn``: ``name`` is capped at 120 chars, and
``range_ctx`` (an arbitrary client-supplied dict persisted as jsonb) is
rejected once its JSON serialization exceeds the size cap -- otherwise a
caller could stash unbounded data in a "filter preset". The cap has to clear
what the filter UI itself can build, which is bounded by an agency's route
count, so the accepting case below is written at that shape.
"""

import pytest
from pydantic import ValidationError

from api.routers.me import _MAX_PRESET_RANGE_CTX_BYTES, PresetIn


def test_preset_in_accepts_name_at_max_length():
    PresetIn(agency_id=1, name="a" * 120, range_ctx={})


def test_preset_in_rejects_name_over_max_length():
    with pytest.raises(ValidationError):
        PresetIn(agency_id=1, name="a" * 121, range_ctx={})


def test_preset_in_accepts_small_range_ctx():
    PresetIn(agency_id=1, name="preset", range_ctx={"from": "2026-01-01", "to": "2026-01-31"})


def test_preset_in_accepts_every_route_of_a_large_network():
    """A picker that can select all of a large agency's routes must not be
    able to build a preset the API then refuses to save."""
    PresetIn(
        agency_id=1,
        name="preset",
        range_ctx={
            "from": "2026-01-01",
            "to": "2026-12-31",
            "dow": "all",
            "time_band": "all",
            "service": "all",
            "routes": [f"route-{i:04d}" for i in range(1000)],
        },
    )


def test_preset_in_rejects_oversized_range_ctx():
    with pytest.raises(ValidationError):
        PresetIn(agency_id=1, name="preset", range_ctx={"blob": "x" * (_MAX_PRESET_RANGE_CTX_BYTES + 1)})
