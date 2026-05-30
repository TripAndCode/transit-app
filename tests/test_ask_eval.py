"""CI gate: ensures the gold-set eval passes (chip + builder coverage = 100%)."""

import subprocess
import sys


def test_ask_eval_passes():
    r = subprocess.run(
        [sys.executable, "scripts/ask_eval.py"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"ask_eval failed:\n{r.stdout}\n{r.stderr}"
