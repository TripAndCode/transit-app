"""Behaviour of the step that reports a failed scheduled run.

Driven through a shimmed `gh` that answers `issue list` from a fixture and
logs every other call, because the step only ever runs on a failed scheduled
run of `main`, long after a pull request could have shown it misbehaving.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "report-scheduled-failure.yml"
TITLE = "Scheduled run failing: Nightly extended"


def _step() -> dict:
    (step,) = yaml.safe_load(WORKFLOW.read_text())["jobs"]["report"]["steps"]
    return step


def _report(tmp_path: Path, open_issues: list[dict]) -> list[list[str]]:
    """Run the step against *open_issues*; the gh calls it made, as argument lists."""
    log = tmp_path / "gh.log"
    issues = tmp_path / "issues.json"
    issues.write_text(json.dumps(open_issues))
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys
        args = sys.argv[1:]
        with open({str(log)!r}, "a") as out:
            out.write(json.dumps(args) + "\\n")
        if args[:2] == ["issue", "list"]:
            print(open({str(issues)!r}).read())
        elif args[:2] not in (["label", "create"], ["issue", "comment"], ["issue", "create"]):
            sys.exit(f"unexpected gh call: {{args}}")
        """)
    )
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{gh.parent}{os.pathsep}{os.environ['PATH']}",
        "WORKFLOW": "Nightly extended",
        "RUN_NUMBER": "41",
        "RUN_URL": "https://github.com/o/r/actions/runs/7",
    }
    subprocess.run(["bash", "-c", _step()["run"]], env=env, check=True)
    return [json.loads(line) for line in log.read_text().splitlines()]


def _create_or_comment(calls: list[list[str]]) -> list[str]:
    (call,) = [args for args in calls if args[:2] in (["issue", "create"], ["issue", "comment"])]
    return call


def test_a_first_failure_opens_a_labelled_issue_naming_the_run(tmp_path):
    call = _create_or_comment(_report(tmp_path, []))
    assert call[:2] == ["issue", "create"]
    assert call[call.index("--title") + 1] == TITLE
    assert call[call.index("--label") + 1] == "scheduled-failure"
    assert "[Nightly extended run #41](https://github.com/o/r/actions/runs/7)" in call[call.index("--body") + 1]


def test_a_later_failure_comments_on_the_open_issue(tmp_path):
    call = _create_or_comment(_report(tmp_path, [{"number": 9, "title": TITLE}]))
    assert call[:3] == ["issue", "comment", "9"]
    assert "https://github.com/o/r/actions/runs/7" in call[call.index("--body") + 1]


def test_another_workflows_open_issue_is_left_alone(tmp_path):
    other = {"number": 9, "title": "Scheduled run failing: secrets-scan-full-history"}
    call = _create_or_comment(_report(tmp_path, [other]))
    assert call[:2] == ["issue", "create"]


def test_the_label_exists_before_an_issue_is_filed_under_it(tmp_path):
    calls = _report(tmp_path, [])
    assert calls[0][:3] == ["label", "create", "scheduled-failure"]
    assert "--force" in calls[0]
