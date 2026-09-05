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
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Stripped so a comma-grouped number ("1,234") extracts as one value instead
# of two unmatched fragments.
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d),(?=\d)")
# The only shape that is a quantified claim about the data rather than an
# identifier or a date span: a number carrying a metric unit. 日/週/月/件 are
# excluded because SYSTEM_PROMPT tells the model to name route_codes and to
# offer periods and a count of suggestions.
# tests/unit/test_hallucination_guard.py pins the accepted spellings.
_SEP = r"[\s,、，-]*"
# A number carrying a metric unit is a claim outright. ``分`` skips the fraction
# reading "N分のM" — の followed by a digit — while still matching the genitive
# 「14.2分の遅れ」, which is a real claim. ``割`` needs no such carve-out: 割引
# and 割り当て are numbers this app cannot ground either (it holds no fare or
# allocation data), so the safe-set check below would reject them anyway.
_METRIC_CLAIM_RE = re.compile(
    r"\d+(?:\.\d+)?" + _SEP + r"(?:%|％|パーセンテージ|パーセント|分(?!の\s*\d)|秒|割)"
    r"|\d+(?:\.\d+)?" + _SEP + r"(?:min(?:ute)?s?|sec(?:ond)?s?|per\s?cent)",
    re.IGNORECASE,
)
# The number shapes SYSTEM_PROMPT actually asks for on the ungrounded path: a
# route_code, a period, a count of suggested questions, a fraction. Anything
# else numeric is a claim by default.
#
# This is the load-bearing half. Enumerating every way to *write* a statistic
# is open-ended — successive review rounds each found another spelling the
# unit list missed — whereas the set of numbers this reply is supposed to
# contain is small, closed, and defined by the prompt we control. The unit
# list above stays because it catches shapes the safe set would otherwise
# admit (a route-code-length number followed by a unit).
_RANGE = r"\d+(?:\s*[〜~–—-]\s*\d+)?\s*"
_SAFE_NUMBER_RES = (
    re.compile(r"\d+\s*分の\s*\d+"),
    re.compile(_RANGE + r"(?:日|週間|週|[かカヶ]月|月|年)"),
    re.compile(_RANGE + r"(?:days?|weeks?|months?|years?)\b", re.IGNORECASE),
    re.compile(_RANGE + r"(?:件|問|つ|個)"),
    re.compile(_RANGE + r"(?:questions?|examples?|suggestions?)\b", re.IGNORECASE),
    re.compile(r"(?<!\d)\d{4,5}(?!\d)"),
)
_ANY_DIGIT_RE = re.compile(r"\d")


def _normalize(text: str) -> str:
    text = _DATE_RE.sub(" ", text)
    return _THOUSANDS_SEP_RE.sub("", text)


def _extract_numbers(text: str) -> list[float]:
    return [float(m) for m in _NUMBER_RE.findall(_normalize(text))]


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
    claimed = _extract_numbers(answer)
    if not claimed:
        return True
    allowed = _flatten_numbers(grounding)
    if not allowed:
        # Nothing to verify against — the turn dispatched no data (e.g. an
        # out-of-scope refusal). Rejecting on any digit rejects the reply the
        # system prompt asks for: a refusal naming concrete route_codes and
        # periods the user *could* ask about. So a number here is a claim
        # unless it is one of the shapes that reply is meant to contain.
        text = _normalize(answer)
        if _METRIC_CLAIM_RE.search(text):
            return False
        for pat in _SAFE_NUMBER_RES:
            text = pat.sub(" ", text)
        return _ANY_DIGIT_RE.search(text) is None
    for number in claimed:
        # Exact match, or a rounded display of an allowed value (nearest int,
        # or nearest 0.1) — a model paraphrasing "14.2" as "about 14" is not
        # a hallucination.
        if any(
            math.isclose(number, a, abs_tol=1e-9)
            or math.isclose(number, round(a), abs_tol=1e-9)
            or math.isclose(number, round(a, 1), abs_tol=1e-9)
            for a in allowed
        ):
            continue
        return False
    return True
