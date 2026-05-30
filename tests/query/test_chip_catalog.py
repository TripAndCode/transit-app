"""Chip catalog static-registry tests."""

from datetime import date

from pipeline.query.chip_catalog import CHIPS, CHIPS_BY_ID, chips_by_category
from pipeline.query.intent import canonicalize


def _ctx():
    return {"from_date": date(2026, 5, 1), "to_date": date(2026, 5, 30)}


def test_chip_count_is_26():
    assert len(CHIPS) == 26


def test_no_duplicate_ids():
    ids = [c.id for c in CHIPS]
    assert len(ids) == len(set(ids))


def test_categories_are_exactly_five():
    """Static catalog has 5 categories; the dynamic ⭐ よく使う isn't here."""
    cats = sorted({c.category for c in CHIPS})
    assert cats == ["compare", "detail", "meta", "ranking", "trend"]


def test_chips_by_id_lookup_works():
    for c in CHIPS:
        assert CHIPS_BY_ID[c.id] is c


def test_chips_by_category_groups_correctly():
    grouped = chips_by_category()
    assert sum(len(v) for v in grouped.values()) == 26
    assert set(grouped.keys()) == {"meta", "ranking", "trend", "compare", "detail"}


def test_every_chip_canonicalizes_cleanly():
    """Every static chip's (tool, args) must canonicalize without ValueError."""
    for c in CHIPS:
        canonicalize(c.tool, c.args, _ctx())


def test_builder_required_chips_have_empty_args():
    """When builder_required=True, the chip opens the builder pre-populated; args should be empty."""
    for c in CHIPS:
        if c.builder_required:
            assert c.args == {}, f"{c.id}: builder_required chips must have empty args"


def test_immutability_frozen_dataclass():
    import pytest

    c = CHIPS[0]
    with pytest.raises(Exception):
        c.id = "tampered"
