"""The parameterized-card gold generator reproduces the committed gold file."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "_gen_phase35_gold.py"
GOLD = ROOT / "tests" / "ask_eval" / "gold_questions.jsonl"

SPEC = importlib.util.spec_from_file_location("gen_phase35_gold", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen)


def test_generator_output_matches_committed_gold():
    generated = [json.dumps(e, ensure_ascii=False) for e in gen.ENTRIES]
    committed = [line for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert generated == committed
