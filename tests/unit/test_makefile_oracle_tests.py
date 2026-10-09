"""15 Oracle-collector bash test suites under oracle_cloud/v3/tests/ have no
runner: nothing in the Makefile or CI invokes them, so a regression there is
silent. `make oracle-tests` runs every suite with a pass/fail summary, and
the nightly workflow wires it into CI so a broken suite is eventually caught.
The same workflow's python-extended job is checked here too, for the setup
steps its gated suites need to run rather than self-skip.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORACLE_TESTS_DIR = ROOT / "oracle_cloud" / "v3" / "tests"


def _oracle_test_suite_names() -> list[str]:
    return sorted(p.name for p in ORACLE_TESTS_DIR.glob("test_*.sh"))


def _makefile_text() -> str:
    return (ROOT / "Makefile").read_text()


def test_oracle_tests_target_exists_and_is_phony():
    text = _makefile_text()
    assert re.search(r"^oracle-tests:", text, re.MULTILINE), "Makefile: no `oracle-tests:` target"
    phony_line = re.search(r"^\.PHONY:.*$", text, re.MULTILINE)
    assert phony_line, "Makefile: no .PHONY line found"
    assert "oracle-tests" in phony_line.group(0).split(), ".PHONY line does not list oracle-tests"


def test_oracle_tests_target_delegates_to_the_runner_script():
    """The recipe must stay a one-liner that calls the script.

    The behaviour that matters — one failing suite fails the run — is tested
    by executing the script below. Inlining the loop back into the recipe
    would put it out of reach of that test, leaving only text assertions,
    which cannot see a failure being swallowed.
    """
    text = _makefile_text()
    match = re.search(r"^oracle-tests:.*?(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, "could not isolate the oracle-tests target body"
    body = match.group(0)
    assert "scripts/run_oracle_tests.sh" in body, "oracle-tests no longer calls the runner script"
    assert "for t in" not in body, "the loop is back in the recipe, where its exit behaviour cannot be tested"


def _run_runner(suite_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_oracle_tests.sh"), str(suite_dir)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def _write_suite(directory: Path, name: str, exit_code: int) -> None:
    suite = directory / name
    suite.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n")
    suite.chmod(0o755)


def test_runner_exits_nonzero_when_any_suite_fails(tmp_path):
    """The property the nightly job depends on, exercised rather than read."""
    _write_suite(tmp_path, "test_ok.sh", 0)
    _write_suite(tmp_path, "test_broken.sh", 1)

    result = _run_runner(tmp_path)

    assert result.returncode != 0, f"a failing suite did not fail the run:\n{result.stdout}"
    assert "FAIL: " in result.stdout and "test_broken.sh" in result.stdout


def test_runner_exits_zero_when_every_suite_passes(tmp_path):
    """The negative control: without it, a runner that always failed would
    satisfy the test above."""
    _write_suite(tmp_path, "test_a.sh", 0)
    _write_suite(tmp_path, "test_b.sh", 0)

    result = _run_runner(tmp_path)

    assert result.returncode == 0, f"all suites passed but the run failed:\n{result.stdout}{result.stderr}"


def test_runner_fails_when_the_directory_holds_no_suites(tmp_path):
    """An empty glob means the directory moved or the pattern broke. Reporting
    success there would let the nightly job go green having run nothing."""
    result = _run_runner(tmp_path)

    assert result.returncode != 0, "an empty suite directory reported success"
    assert "no suites found" in result.stderr


def test_oracle_tests_target_covers_every_suite_on_disk():
    suites = _oracle_test_suite_names()
    assert len(suites) == 15, f"expected 15 oracle bash suites, found {len(suites)}: {suites}"


def test_nightly_workflow_references_oracle_tests():
    workflow = ROOT / ".github" / "workflows" / "nightly-extended.yml"
    assert workflow.exists(), "missing .github/workflows/nightly-extended.yml"
    text = workflow.read_text()
    assert "make oracle-tests" in text, "nightly-extended.yml does not run `make oracle-tests`"
    assert re.search(r"^\s*schedule:\s*$", text, re.MULTILINE), "nightly-extended.yml has no schedule trigger"
    assert "workflow_dispatch" in text
    assert "RUN_SLOW" in text and "RUN_I18N_SCAN" in text and "RUN_DASHBOARD_E2E_SCAN" in text
    assert "RUN_CH_INTEGRATION" in text
    assert "permissions:" in text and "contents: read" in text
    assert "timeout-minutes: 60" in text


def _python_extended_step_tokens() -> list[list[str]]:
    """Each python-extended step's `run` script, shell-tokenized, in step order."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "nightly-extended.yml").read_text())
    return [shlex.split(step.get("run", "")) for step in workflow["jobs"]["python-extended"]["steps"]]


def _contains(tokens: list[str], command: list[str]) -> bool:
    return any(tokens[i : i + len(command)] == command for i in range(len(tokens) - len(command) + 1))


def _first_step_running(command: list[str]) -> int:
    for i, tokens in enumerate(_python_extended_step_tokens()):
        if _contains(tokens, command):
            return i
    raise AssertionError(f"python-extended job never runs `{shlex.join(command)}`")


def test_python_extended_job_installs_embeddings_group():
    """RUN_SLOW makes test_embeddings a required non-skip; without the group
    the embedder import fails and that test fails instead of skipping."""
    step = _python_extended_step_tokens()[_first_step_running(["poetry", "install"])]
    assert _contains(step, ["--with", "embeddings"]), "python-extended's `poetry install` lacks `--with embeddings`"


def test_python_extended_job_bakes_spa_between_build_and_pytest():
    """The gated browser suites open api/static/index.html, which only
    `make bake` fills from the build output; without it they self-skip."""
    build = _first_step_running(["npm", "run", "build"])
    bake = _first_step_running(["make", "bake"])
    pytest_run = _first_step_running(["poetry", "run", "pytest"])
    assert build < bake < pytest_run, "python-extended must run `make bake` after the SPA build and before pytest"
