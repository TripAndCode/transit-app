"""The frontend job's coverage gate: CI runs the suite through `test:coverage`,
so the thresholds in vitest.config.ts fail a pull request that drops below them.

Each half is only a gate with the other. Thresholds that nothing runs enforce
nothing, and a coverage run whose thresholds are gone reports a number and
passes whatever it is.
"""

from __future__ import annotations

import json
import re
import shlex

from tests.unit.test_ci_workflow_gates import ROOT, _workflow_yaml

FRONTEND = ROOT / "frontend"
METRICS = ("lines", "statements", "branches", "functions")


def _frontend_commands() -> list[list[str]]:
    commands = []
    for step in _workflow_yaml()["jobs"]["frontend"]["steps"]:
        for line in step.get("run", "").splitlines():
            argv = shlex.split(line, comments=True)
            if argv:
                commands.append(argv)
    return commands


def test_frontend_job_runs_the_suite_through_the_coverage_script() -> None:
    commands = _frontend_commands()
    assert ["npm", "run", "test:coverage"] in commands, "the frontend job never runs the coverage thresholds"
    assert ["npm", "run", "test"] not in commands, (
        "the coverage run already is the test run; a second plain pass doubles the job's slowest step"
    )


def test_coverage_script_takes_its_thresholds_from_the_config() -> None:
    """A `--coverage.*` override on the script (`thresholds.lines=0`,
    `enabled=false`) would empty the gate without touching the config."""
    argv = shlex.split(json.loads((FRONTEND / "package.json").read_text())["scripts"]["test:coverage"])
    assert argv[:2] == ["vitest", "run"]
    assert [arg for arg in argv if arg.startswith("--coverage")] == ["--coverage"]


def test_vitest_config_declares_positive_thresholds() -> None:
    config = "\n".join(
        line for line in (FRONTEND / "vitest.config.ts").read_text().splitlines() if not line.lstrip().startswith("//")
    )
    block = re.search(r"\bthresholds:\s*\{([^{}]*)\}", config)
    assert block, "vitest.config.ts declares no coverage thresholds, so the coverage run gates nothing"
    values = {name: float(value) for name, value in re.findall(r"\b(\w+):\s*(\d+(?:\.\d+)?)", block.group(1))}
    declared = {name: value for name, value in values.items() if name in METRICS}
    assert declared, f"no threshold names one of {METRICS}: {block.group(1).strip()!r}"
    assert all(value > 0 for value in declared.values()), f"a zero threshold gates nothing: {declared}"
