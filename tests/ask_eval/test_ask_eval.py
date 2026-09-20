"""CI gate: ensures the gold-set eval passes (chip + builder coverage = 100%)."""

import os
import subprocess
import sys
from pathlib import Path


def test_ask_eval_passes():
    # The interpreter already running this suite, not `poetry run`, which
    # resolves its venv by cwd and so picks an unprovisioned one in a worktree.
    project_root = Path(__file__).parent.parent.parent
    r = subprocess.run(
        [sys.executable, "scripts/ask_eval.py"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env={**os.environ, "PYTHONPATH": str(project_root)},
    )
    assert r.returncode == 0, f"ask_eval failed:\n{r.stdout}\n{r.stderr}"
