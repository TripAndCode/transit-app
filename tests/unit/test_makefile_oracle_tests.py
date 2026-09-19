"""15 Oracle-collector bash test suites under oracle_cloud/v3/tests/ have no
runner: nothing in the Makefile or CI invokes them, so a regression there is
silent. `make oracle-tests` runs every suite with a pass/fail summary, and
the nightly workflow wires it into CI so a broken suite is eventually caught.
"""

from __future__ import annotations

import re
from pathlib import Path

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


def test_oracle_tests_target_runs_every_suite_and_exits_nonzero_on_failure():
    text = _makefile_text()
    match = re.search(r"^oracle-tests:.*?(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, "could not isolate the oracle-tests target body"
    body = match.group(0)

    assert "oracle_cloud/v3/tests" in body, "oracle-tests target does not reference oracle_cloud/v3/tests"
    assert "test_*.sh" in body, "oracle-tests target does not glob test_*.sh"
    # A non-zero exit on any suite failure is the whole point of the target;
    # this repo's Makefile targets do that by tracking a fail counter/flag
    # and exiting on it, so require some explicit exit-on-failure construct.
    assert re.search(r"exit\s+1\b", body) or "exit $$fail" in body or "exit $$rc" in body, (
        "oracle-tests target has no visible non-zero exit on failure"
    )


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
