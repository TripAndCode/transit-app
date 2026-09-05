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
# A number carrying one of this app's metric units — the only shape that is a
# quantified claim about the data rather than an identifier or a date span.
# Deliberately excludes 日/週/月 and 件: SYSTEM_PROMPT tells the model to name
# route_codes (4-5 bare digits) and to offer periods and a count of suggested
# questions, so those digits are the prompt working as designed, not claims.
# ``割`` excludes 割引/割合 so a fare discount or the word "proportion" is not
# read as "N tenths". Units are matched as words, not glyphs only: パーセント is
# the ordinary prose form of % in Japanese, and a claim spelled that way is the
# same claim.
_METRIC_CLAIM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|パーセンテージ|パーセント|分|秒|割(?![引合]))"
    r"|\d+(?:\.\d+)?\s*(?:min(?:ute)?s?|sec(?:ond)?s?|per\s?cent)\b",
    re.IGNORECASE,
)


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
        # out-of-scope refusal). Every digit is unverifiable here by
        # definition, so rejecting on any digit rejects the reply the system
        # prompt asks for: a refusal that names concrete route_codes and
        # periods the user *could* ask about. Only a unit-bearing metric claim
        # is treated as a fabrication on this path.
        return _METRIC_CLAIM_RE.search(_normalize(answer)) is None
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
