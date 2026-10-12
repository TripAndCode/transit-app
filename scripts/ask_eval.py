"""Check that every gold entry's stored signature survives canonicalization.

Each gold entry holds a tool, its canonical args and the signature hash of
both. The check re-canonicalizes the stored args and requires the same hash, so
it fails when `canonicalize` or `signature_hash` changes output for inputs the
gold set already pins. It opens no database and calls no router, builder or LLM.
Regenerate the gold set with `scripts/_gen_phase35_gold.py` after an intended
change.

Usage:
    poetry run python scripts/ask_eval.py

Exit codes:
    0 — builder_coverage 100%
    1 — a hash differs, an entry has an unknown `via`, or the set is too small
    2 — gold JSONL not found
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from pipeline.query.intent import canonicalize, signature_hash

EVAL_CTX = {"from_date": date(2026, 5, 1), "to_date": date(2026, 5, 30)}
_MIN_BUILDER_ENTRIES = 20


def _hash(tool: str, args: dict) -> str:
    return signature_hash(tool, canonicalize(tool, args, EVAL_CTX))


def main() -> int:
    path = Path("tests/ask_eval/gold_questions.jsonl")
    if not path.exists():
        print(f"missing {path}", file=sys.stderr)
        return 2

    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    builder_pass = builder_total = 0
    misses: list[str] = []

    for e in entries:
        if e["via"] != "builder":
            misses.append(f"{e['id']}: unknown via {e['via']!r}")
            continue
        expected_tool = e["expected_tool"]
        expected_args = e["expected_args_canonical"]
        expected_hash = signature_hash(expected_tool, expected_args)
        builder_total += 1
        actual = _hash(expected_tool, expected_args)
        if actual == expected_hash:
            builder_pass += 1
        else:
            misses.append(f"{e['id']}: builder hash {actual} != expected {expected_hash}")

    pct = f"{builder_pass}/{builder_total} ({100 * builder_pass / builder_total:.1f}%)" if builder_total else "0/0"
    print(f"builder_coverage:     {pct}")

    if misses:
        print("\nMISSES:")
        for m in misses[:20]:
            print(f"  - {m}")
        if len(misses) > 20:
            print(f"  ... and {len(misses) - 20} more")

    if builder_total < _MIN_BUILDER_ENTRIES:
        msg = f"gold set has {builder_total} builder entries; expected >= {_MIN_BUILDER_ENTRIES}"
        print(f"\nERROR: {msg}", file=sys.stderr)
        return 1
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
