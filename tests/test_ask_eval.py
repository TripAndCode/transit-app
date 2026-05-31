"""CI gate: ensures the gold-set eval passes (chip + builder coverage = 100%)."""

import os
import subprocess
import sys
from pathlib import Path


def test_ask_eval_passes():
    # Use the poetry-managed virtualenv python if available, so the project
    # packages (pipeline.*) are importable. Fall back to sys.executable for
    # environments where the venv is already active (e.g. CI with poetry run).
    import shutil

    project_root = Path(__file__).parent.parent
    poetry_exe = shutil.which("poetry")
    if poetry_exe:
        r = subprocess.run(
            [poetry_exe, "run", "python", "scripts/ask_eval.py"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env={**os.environ},
        )
    else:
        # Fallback: sys.executable with project root on PYTHONPATH.
        env = {**os.environ, "PYTHONPATH": str(project_root)}
        r = subprocess.run(
            [sys.executable, "scripts/ask_eval.py"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env=env,
        )
    assert r.returncode == 0, f"ask_eval failed:\n{r.stdout}\n{r.stderr}"
