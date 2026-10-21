"""Post-generation numeric verification for free-form LLM answers.

Structural defense-in-depth alongside the template-only proactive path
(``pipeline.query.copilot_templates``): a system-prompt instruction not to
invent numbers is not a guarantee, so every number the model actually wrote
is cross-checked here against the grounding data it was given, and the
answer is discarded (never shown) if any number doesn't trace back.
"""

from __future__ import annotations

import decimal
import math
import re

# Two alternatives so a leading "-" is only ever read as a sign when it isn't
# glued to a preceding word character: the first alternative requires that
# non-word lookbehind before matching a negative number, the second matches a
# plain digit run unconditionally. Without the split, a single lookbehind in
# front of the whole pattern would also suppress a *positive* number glued to
# a preceding letter (e.g. "route14" or the "14" in "route-14"), since the
# lookbehind sits before the optional sign either way.
_NUMBER_RE = re.compile(r"(?<!\w)-\d+\.?\d*|\d+\.?\d*")
# Stripped before number extraction so an ISO date's own day/month/year
# components (e.g. the "2026"/"09"/"05" in "2026-09-05") never leak into the
# allowed number set as spurious grounded values — a small date component
# coinciding with a genuinely fabricated number in the answer would otherwise
# let that fabrication pass verification.
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# Stripped so a comma-grouped number ("1,234") extracts as one value instead
# of two unmatched fragments.
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d),(?=\d)")
# A numbered list's markers ("1. ", "2) ", "3．") order the answer's lines;
# they claim nothing about the data. An ASCII "." or ")" counts only before a
# space, since "1.5" opening a line is a value; a full-width one is never part
# of a number.
_LIST_ORDINAL_RE = re.compile(r"^[ \t]*(\d+)(?:[.)][ \t]|[．）])", re.MULTILINE)
# A Japanese date ("9月5日", "2026年9月5日"), not one read out of a longer
# number, a decimal or a year that is not four digits. One naming a grounded day leaves the claims, since the
# grounding's ISO dates are stripped before number extraction.
_JA_DATE_RE = re.compile(r"(?<!\d)(?<!\d[.,-])(?<!\d年)(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日")
_PERCENT_SIGN_RE = re.compile(r"[ \t]?[%％]")


def _without_list_ordinals(answer: str) -> str:
    """``answer`` with its numbered list's markers removed, when it has one:
    markers numbering two or more lines 1, 2, 3, ... in order. A lone
    line-leading "99) ..." is a number like any other, so it stays a claim."""
    markers = [int(m.group(1)) for m in _LIST_ORDINAL_RE.finditer(answer)]
    if len(markers) < 2 or markers != list(range(1, len(markers) + 1)):
        return answer
    return _LIST_ORDINAL_RE.sub("", answer)


def _extract_numbers(text: str) -> list[float]:
    return [value for value, _ in _extract_claims(text)]


def _extract_claims(text: str) -> list[tuple[float, bool]]:
    """Each number in *text*, and whether a percent sign follows it."""
    # A non-word, non-blank placeholder: numbers read around it as around a
    # space, but no percent sign past a stripped date attaches to them.
    text = _DATE_RE.sub("\x00", text)
    text = _THOUSANDS_SEP_RE.sub("", text)
    return [(float(m.group()), bool(_PERCENT_SIGN_RE.match(text, m.end()))) for m in _NUMBER_RE.finditer(text)]


def _grounded_dates(value: object) -> set[tuple[int | None, int, int]]:
    """The days *value* names, as ISO dates or in Japanese (the echoed question
    names them so). A grounded date without a year grounds only an answer's
    date without one."""
    out: set[tuple[int | None, int, int]] = set()
    if isinstance(value, dict):
        for v in value.values():
            out |= _grounded_dates(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= _grounded_dates(v)
    elif isinstance(value, str):
        out.update((int(y), int(m), int(d)) for y, m, d in _DATE_RE.findall(value))
        out.update((int(y) if y else None, int(m), int(d)) for y, m, d in _JA_DATE_RE.findall(value))
    return out


def _without_grounded_dates(answer: str, grounding: dict) -> str:
    """``answer`` with each Japanese date of a grounded day removed. Any other
    date stays, so its month and day are still claims that must trace to the
    grounding like any number."""
    dates = _grounded_dates(grounding)

    def strip_grounded(match: re.Match[str]) -> str:
        year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
        if any(m == month and d == day and (year is None or (y is not None and int(year) == y)) for y, m, d in dates):
            # The date's own last character, not a space: what follows it then
            # reads as it did unstripped (a "-7" stays a range, not a sign, and
            # a percent sign past it attaches to no number before it).
            return "日"
        return match.group()

    return _JA_DATE_RE.sub(strip_grounded, answer) if dates else answer


def _traces_to(number: float, allowed: set[float]) -> bool:
    # Exact match, or a rounded display of an allowed value (nearest int, or
    # nearest 0.1) -- a model paraphrasing "14.2" as "about 14" is not a
    # hallucination.
    return any(
        math.isclose(number, a, abs_tol=1e-9)
        or math.isclose(number, round(a), abs_tol=1e-9)
        or math.isclose(number, round(a, 1), abs_tol=1e-9)
        for a in allowed
    )


def _flatten_numbers(value: object) -> set[float]:
    out: set[float] = set()
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float, decimal.Decimal)):
        out.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            out |= _flatten_numbers(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= _flatten_numbers(v)
    elif isinstance(value, str):
        out.update(_extract_numbers(value))
    return out


def verify_numeric_claims(answer: str, grounding: dict) -> bool:
    claimed = _extract_claims(_without_grounded_dates(_without_list_ordinals(answer), grounding))
    if not claimed:
        return True
    allowed = _flatten_numbers(grounding)
    if not allowed:
        # No grounding means no basis for a verdict, so report no violation
        # rather than invent one. Numbers legitimately appear in an ungrounded
        # reply as route codes, periods and counts (SYSTEM_PROMPT rules 2 and
        # 3); deciding that case belongs in a mechanism that constrains what
        # the model may return, not in a verifier.
        return True
    # A rate grounded as a fraction (0.35) is fairly written as a percentage
    # (35%), but only with the percent sign: a bare 35 claims something else,
    # and a whole number (a count of 3) is no rate to write as 300%.
    as_percent = {a * 100 for a in allowed if 0 < a < 1}
    for number, is_percent in claimed:
        if _traces_to(number, allowed) or (is_percent and _traces_to(number, as_percent)):
            continue
        return False
    return True
