import pytest
from fastapi import HTTPException

from pipeline.query.followup_context import select_context_row


def test_selection_beyond_preview_is_included_without_copying_whole_table():
    result = {"kind": "table", "columns": ["id", "avg_min"], "rows": [[i, None] for i in range(120)],
              "summary": "Global summary"}
    selected = select_context_row(result, 100)
    assert selected is not None
    assert selected["rows"] == [[100, None]]
    assert selected["summary"] == ""
    assert len(result["rows"]) == 120
    assert result["summary"] == "Global summary"


@pytest.mark.parametrize("index", [-1, 1, True, "0"])
def test_invalid_selection_never_falls_back_to_different_evidence(index):
    with pytest.raises(HTTPException) as exc:
        select_context_row({"kind": "table", "rows": [[0]]}, index)
    assert exc.value.status_code == 400


def test_unselected_context_is_unchanged_and_missing_result_rejected():
    result = {"kind": "text"}
    assert select_context_row(result, None) is result
    with pytest.raises(HTTPException):
        select_context_row(None, 0)
