"""CSV cells a spreadsheet shows rather than runs.

Feed-written and admin-written text (route codes, stop and hub names) reaches
exported files, and a cell that opens like a formula would run when the file
is opened. A leading quote keeps such a cell a string.
"""

from __future__ import annotations

from typing import TypeVar

FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

T = TypeVar("T")


def inert(value: T) -> T | str:
    """*value* with a leading quote when it is text that opens like a formula;
    any other value as it is, so a number stays a number."""
    if isinstance(value, str) and value.startswith(FORMULA_LEADS):
        return f"'{value}"
    return value
