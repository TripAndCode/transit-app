"""Every module of ``pipeline.query`` and ``pipeline.reports`` imports on its own.

The two packages import each other, so a module that works only after another
entry point has loaded the rest is an import cycle waiting for the first caller
to import it directly: a script, a test module, a worker. Each import runs in a
fresh interpreter, because a module already in ``sys.modules`` would mask it.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("pipeline/query", "pipeline/reports")


def _modules() -> list[str]:
    names = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).glob("*.py")):
            parts = path.relative_to(ROOT).with_suffix("").parts
            names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def _import_error(module: str) -> str | None:
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode == 0:
        return None
    last_line = (proc.stderr.strip().splitlines() or [f"exit code {proc.returncode}"])[-1]
    return f"{module}: {last_line}"


def test_every_query_and_reports_module_imports_in_a_fresh_interpreter():
    with ThreadPoolExecutor(max_workers=8) as pool:
        failures = [error for error in pool.map(_import_error, _modules()) if error]
    assert not failures, "\n".join(failures)
