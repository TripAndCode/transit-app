"""Bounded, source-derived evidence selection for follow-up questions."""

from fastapi import HTTPException


def select_context_row(result: dict | None, index: int | None) -> dict | None:
    if index is None:
        return result
    rows = result.get("rows") if result else None
    if (
        not result
        or result.get("kind") != "table"
        or not isinstance(rows, list)
        or isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(rows)
    ):
        raise HTTPException(status_code=400, detail="invalid_context_row")
    # A full-table summary may assert facts not supported by the selected row.
    return {**result, "summary": "", "rows": [rows[index]]}
