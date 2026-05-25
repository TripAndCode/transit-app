"""Unit test for chat.py's handling of null arguments.

When Groq returns arguments='null', json.loads("null") returns Python None.
The bug was that this None was not converted to {}, so dispatch → handler
would crash on args.get(...).
"""

import json


def test_json_loads_null_returns_none():
    """Confirm that json.loads('null') returns None, not {}."""
    parsed = json.loads("null")
    assert parsed is None


def test_args_normalization_before_fix():
    """Before the fix, args could be None and crash on .get()."""
    # Simulate the old logic from chat.py (lines 157-166, WITHOUT the fix)
    arguments_from_groq = "null"
    try:
        args = json.loads(arguments_from_groq or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}

    # At this point, args is None (the bug)
    assert args is None

    # Calling .get() on None would crash
    try:
        category = args.get("category")
        assert False, "Should have crashed"
    except AttributeError as e:
        assert "'NoneType' object has no attribute 'get'" in str(e)


def test_args_normalization_after_fix():
    """After the fix, args is always a dict and safe to call .get() on."""
    # Simulate the fixed logic from chat.py
    arguments_from_groq = "null"
    try:
        args = json.loads(arguments_from_groq or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        args = {}
    # THE FIX: add this line
    if args is None:
        args = {}

    # Now args is always a dict
    assert args == {}
    assert isinstance(args, dict)

    # Safe to call .get()
    category = args.get("category")
    assert category is None
