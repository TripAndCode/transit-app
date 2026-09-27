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
#: The floors this gate enforces; a change to the set is a policy change, made here.
REQUIRED_METRICS = {"lines", "statements"}
#: Keys that keep the coverage step in the workflow without it failing the check.
_DISARMING_KEYS = ("if", "continue-on-error")


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


def test_the_coverage_step_always_runs_and_can_fail() -> None:
    """An `if:` on the step or the job would keep the command in the file
    while CI stops running it, and `continue-on-error` would keep running it
    while a failure no longer fails the check."""
    job = _workflow_yaml()["jobs"]["frontend"]
    for key in _DISARMING_KEYS:
        assert key not in job, f"the frontend job sets {key}: {job[key]!r}"
    steps = [
        step
        for step in job["steps"]
        if ["npm", "run", "test:coverage"]
        in (shlex.split(line, comments=True) for line in step.get("run", "").splitlines())
    ]
    assert steps, "no frontend step runs the coverage script"
    for step in steps:
        for key in _DISARMING_KEYS:
            assert key not in step, f"the coverage step sets {key}: {step[key]!r}"


def test_coverage_script_takes_its_thresholds_from_the_config() -> None:
    """A `--coverage.*` override on the script (`thresholds.lines=0`,
    `enabled=false`) would empty the gate without touching the config."""
    argv = shlex.split(json.loads((FRONTEND / "package.json").read_text())["scripts"]["test:coverage"])
    assert argv[:2] == ["vitest", "run"]
    assert [arg for arg in argv if arg.startswith("--coverage")] == ["--coverage"]


def _coverage_block() -> str:
    """The body of vitest.config.ts's `coverage: { ... }`, commented-out lines
    dropped. Braces inside string literals (globs such as `*.{ts,tsx}`) are
    skipped so they cannot end the block early."""
    config = "\n".join(
        line for line in (FRONTEND / "vitest.config.ts").read_text().splitlines() if not line.lstrip().startswith("//")
    )
    start = re.search(r"\bcoverage:\s*\{", config)
    assert start, "vitest.config.ts has no `coverage` block, so the coverage run gates nothing"
    depth, quote, i = 0, "", start.end() - 1
    while i < len(config):
        char = config[i]
        if quote:
            if char == "\\":
                i += 1
            elif char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return config[start.end() : i]
        i += 1
    raise AssertionError("vitest.config.ts's `coverage` block is never closed")


def test_vitest_config_declares_positive_thresholds() -> None:
    block = re.search(r"\bthresholds:\s*\{([^{}]*)\}", _coverage_block())
    assert block, "vitest.config.ts declares no coverage thresholds, so the coverage run gates nothing"
    values = {name: float(value) for name, value in re.findall(r"\b(\w+):\s*(\d+(?:\.\d+)?)", block.group(1))}
    declared = {name: value for name, value in values.items() if name in METRICS}
    assert declared, f"no threshold names one of {METRICS}: {block.group(1).strip()!r}"
    missing = REQUIRED_METRICS - declared.keys()
    assert not missing, f"the gate no longer enforces {sorted(missing)}: {declared}"
    assert all(value > 0 for value in declared.values()), f"a zero threshold gates nothing: {declared}"


def test_vitest_config_does_not_narrow_the_measured_files() -> None:
    """The thresholds are percentages of whatever is measured, so an `include`
    naming one well-tested file satisfies them while the gate checks nothing."""
    narrowing = re.findall(r"""["']?\b(include|exclude)\b["']?\s*:""", _coverage_block())
    assert not narrowing, (
        f"the coverage block declares {sorted(set(narrowing))}, which narrows the files the thresholds "
        "measure; that must be a deliberate, reviewed change, made here together with this test"
    )
