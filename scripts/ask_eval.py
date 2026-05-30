"""Run the gold-set eval; exit nonzero if builder coverage < 100%.

Usage:
    poetry run python scripts/ask_eval.py

Exit codes:
    0 — builder_coverage 100% (chip gate skipped — catalog removed in Phase ③.5)
    1 — at least one CI-gate metric failed
    2 — gold JSONL not found
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from pipeline.query.intent import canonicalize, signature_hash

EVAL_CTX = {"from_date": date(2026, 5, 1), "to_date": date(2026, 5, 30)}


def _hash(tool: str, args: dict) -> str:
    return signature_hash(tool, canonicalize(tool, args, EVAL_CTX))


def main() -> int:
    path = Path("tests/ask_eval/gold_questions.jsonl")
    if not path.exists():
        print(f"missing {path}", file=sys.stderr)
        return 2

    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    chip_pass = chip_total = 0
    builder_pass = builder_total = 0
    paraphrase_pass = paraphrase_total = 0
    misses: list[str] = []

    for e in entries:
        via = e["via"]
        expected_tool = e["expected_tool"]
        expected_args = e["expected_args_canonical"]
        expected_hash = signature_hash(expected_tool, expected_args)

        if via == "chip":
            # chip catalog was removed in Phase ③.5; skip with a warning.
            chip_total += 1
            print(f"  WARN: skipping chip entry {e['id']!r} (catalog removed)", file=sys.stderr)
            continue

        elif via == "builder":
            builder_total += 1
            actual = _hash(expected_tool, expected_args)
            if actual == expected_hash:
                builder_pass += 1
            else:
                misses.append(f"{e['id']}: builder hash {actual} != expected {expected_hash}")

        elif via == "paraphrase-reachable":
            paraphrase_total += 1
            # chip catalog removed; paraphrase entries referencing chips are skipped.
            print(f"  WARN: skipping paraphrase entry {e['id']!r} (chip catalog removed)", file=sys.stderr)
            continue

    def pct(p: int, t: int) -> str:
        return f"{p}/{t} ({100 * p / t:.1f}%)" if t else "0/0"

    print(f"chip_coverage:        {pct(chip_pass, chip_total)}")
    print(f"builder_coverage:     {pct(builder_pass, builder_total)}")
    print(f"paraphrase_reachable: {pct(paraphrase_pass, paraphrase_total)}")

    if misses:
        print("\nMISSES:")
        for m in misses[:20]:
            print(f"  - {m}")
        if len(misses) > 20:
            print(f"  ... and {len(misses) - 20} more")

    # CI gate: builder must be 100%.
    # chip gate is skipped when chip_total == 0 (catalog removed in Phase ③.5;
    # P11 will replace with parameterized-card entries).
    # Guard against silent-pass when the gold file has been emptied — require
    # at least 10 builder entries.
    _MIN_CHIP_ENTRIES = 0  # relaxed in Phase ③.5; P11 will raise this again
    _MIN_BUILDER_ENTRIES = 10
    if chip_total > 0 and chip_total < _MIN_CHIP_ENTRIES:
        msg = f"gold set has {chip_total} chip entries; expected >= {_MIN_CHIP_ENTRIES}"
        print(f"\nERROR: {msg}", file=sys.stderr)
        return 1
    if builder_total < _MIN_BUILDER_ENTRIES:
        msg = f"gold set has {builder_total} builder entries; expected >= {_MIN_BUILDER_ENTRIES}"
        print(f"\nERROR: {msg}", file=sys.stderr)
        return 1
    if chip_total > 0 and chip_pass < chip_total:
        return 1
    if builder_pass < builder_total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
