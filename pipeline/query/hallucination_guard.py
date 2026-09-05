"""Post-generation numeric verification for free-form LLM answers.

Structural defense-in-depth alongside the template-only proactive path
(``pipeline.query.copilot_templates``): a system-prompt instruction not to
invent numbers is not a guarantee, so every number the model actually wrote
is cross-checked here against the grounding data it was given, and the
answer is discarded (never shown) if any number doesn't trace back.
"""

from __future__ import annotations

import math
import re

# A lookbehind excluding any word character (not just a digit) before a
# leading "-" keeps a hyphen-glued token like "route-14" from being misread
# as the negative number -14.
_NUMBER_RE = re.compile(r"(?<!\w)-?\d+\.?\d*")
# Stripped before number extraction so an ISO date's own hyphens (e.g. the
# "09"/"05" in "2026-09-05") never leak into the allowed/claimed number sets
# as arbitrary negative-number parsing artifacts.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Stripped so a comma-grouped number ("1,234") extracts as one value instead
# of two unmatched fragments.
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d),(?=\d)")


def _extract_numbers(text: str) -> list[float]:
    text = _DATE_RE.sub(" ", text)
    text = _THOUSANDS_SEP_RE.sub("", text)
    return [float(m) for m in _NUMBER_RE.findall(text)]


def _flatten_numbers(value: object) -> set[float]:
    out: set[float] = set()
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
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
        return False
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
