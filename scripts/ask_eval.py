"""Run the gold-set eval; exit nonzero if chip or builder coverage < 100%.

Usage:
    poetry run python scripts/ask_eval.py

Exit codes:
    0 — chip_coverage and builder_coverage both 100%
    1 — at least one CI-gate metric failed
    2 — gold JSONL not found
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from pipeline.query.chip_catalog import CHIPS_BY_ID
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
            chip_total += 1
            chip = CHIPS_BY_ID.get(e["chip_id"])
            if chip is None:
                misses.append(f"{e['id']}: chip {e['chip_id']!r} not in catalog")
                continue
            actual = _hash(chip.tool, chip.args)
            if actual == expected_hash:
                chip_pass += 1
            else:
                misses.append(f"{e['id']}: chip {chip.id} hash {actual} != expected {expected_hash}")

        elif via == "builder":
            builder_total += 1
            actual = _hash(expected_tool, expected_args)
            if actual == expected_hash:
                builder_pass += 1
            else:
                misses.append(f"{e['id']}: builder hash {actual} != expected {expected_hash}")

        elif via == "paraphrase-reachable":
            paraphrase_total += 1
            reachable_id = e.get("reachable_via_chip")
            if reachable_id and reachable_id in CHIPS_BY_ID:
                chip = CHIPS_BY_ID[reachable_id]
                actual = _hash(chip.tool, chip.args)
                if actual == expected_hash:
                    paraphrase_pass += 1
                else:
                    misses.append(
                        f"{e['id']}: reachable_via_chip {reachable_id} hash {actual} != expected {expected_hash}"
                    )
            else:
                misses.append(f"{e['id']}: no reachable_via_chip pointer")

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

    # CI gate: chip + builder must be 100%. Guard against silent-pass when the
    # gold file has somehow been emptied — require at least 20 chip entries +
    # 10 builder entries, matching the spec's promised v1 coverage.
    _MIN_CHIP_ENTRIES = 20
    _MIN_BUILDER_ENTRIES = 10
    if chip_total < _MIN_CHIP_ENTRIES:
        msg = f"gold set has {chip_total} chip entries; expected >= {_MIN_CHIP_ENTRIES}"
        print(f"\nERROR: {msg}", file=sys.stderr)
        return 1
    if builder_total < _MIN_BUILDER_ENTRIES:
        msg = f"gold set has {builder_total} builder entries; expected >= {_MIN_BUILDER_ENTRIES}"
        print(f"\nERROR: {msg}", file=sys.stderr)
        return 1
    if chip_pass < chip_total:
        return 1
    if builder_pass < builder_total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
