"""Exported cells that open like a formula are written as text."""

import pytest

from api.csv_cells import inert


@pytest.mark.parametrize("cell", ["=1+1", "+cmd", "-2", "@x", "\ttab", "\rcr"])
def test_text_opening_like_a_formula_gets_a_leading_quote(cell):
    assert inert(cell) == f"'{cell}"


def test_other_text_and_every_number_stay_as_they_are():
    assert inert("50") == "50"
    assert inert("乗継ぎ：=name") == "乗継ぎ：=name"
    assert inert(-0.4) == -0.4
    assert inert(None) is None
