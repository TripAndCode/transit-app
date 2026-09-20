"""CI gate: ensures the gold-set eval passes (chip + builder coverage = 100%)."""

import os
import subprocess
import sys
from pathlib import Path


def test_ask_eval_passes():
    # Run the script with the interpreter already running this suite, rather
    # than re-resolving one through `poetry run`. Poetry picks its virtualenv
    # from the current directory's identity, so from a git worktree it selects
    # a *different* environment than the one pytest was launched from — one
    # that has no project dependencies installed, making this fail with
    # ModuleNotFoundError regardless of the code under test. sys.executable is
    # by definition the environment the suite imports the project from.
    project_root = Path(__file__).parent.parent.parent
    r = subprocess.run(
        [sys.executable, "scripts/ask_eval.py"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env={**os.environ, "PYTHONPATH": str(project_root)},
    )
    assert r.returncode == 0, f"ask_eval failed:\n{r.stdout}\n{r.stderr}"
