"""The Transfers CSV writes every preamble cell so that a spreadsheet shows
it rather than runs it."""

import csv
import io

from api.routers.transfers import csv_text


def test_a_preamble_cell_that_opens_like_a_formula_is_written_as_text():
    text = csv_text(["=1+1", "+cmd", "-2", "@x", "\ttab", "乗継ぎ：=name"], [["2026-10-01", 1]])
    cells = [row[0] for row in csv.reader(io.StringIO(text.lstrip("﻿")))]
    assert cells[:6] == ["'=1+1", "'+cmd", "'-2", "'@x", "'\ttab", "乗継ぎ：=name"]
    assert cells[-1] == "2026-10-01"
