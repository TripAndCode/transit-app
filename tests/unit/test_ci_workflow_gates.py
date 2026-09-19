"""CI hardening invariants for `.github/workflows/ci.yml`.

Three docs (README, docs/features, docs/refactor-log) call `npm run lint:i18n`
and `npm run lint:i18n-strings` mandatory gates, but nothing actually ran them
in CI, so a key-parity or exact-string regression could merge unnoticed. The
workflow also lacked the baseline hardening (concurrency de-dup, least-
privilege permissions, job timeouts) every other workflow in this repo has,
and carried a "free disk space" step that can only ever fire on a
GitHub-hosted runner this repo doesn't use.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text()


def _workflow_yaml() -> dict:
    return yaml.safe_load(_workflow_text())


def test_frontend_job_runs_both_i18n_gates() -> None:
    text = _workflow_text()
    assert "npm run lint:i18n" in text
    assert "npm run lint:i18n-strings" in text


def test_i18n_gates_run_in_frontend_job() -> None:
    workflow = _workflow_yaml()
    frontend_steps = workflow["jobs"]["frontend"]["steps"]
    run_commands = " ".join(step.get("run", "") for step in frontend_steps)
    assert "npm run lint:i18n-strings" in run_commands
    assert "npm run lint:i18n" in run_commands.replace("npm run lint:i18n-strings", "")


def test_top_level_concurrency_group_cancels_in_progress() -> None:
    workflow = _workflow_yaml()
    concurrency = workflow.get("concurrency")
    assert concurrency is not None, "workflow is missing a top-level `concurrency` block"
    assert concurrency.get("cancel-in-progress") is True


def test_top_level_permissions_are_least_privilege() -> None:
    workflow = _workflow_yaml()
    permissions = workflow.get("permissions")
    assert permissions is not None, "workflow is missing a top-level `permissions` block"
    assert permissions.get("contents") == "read"


def test_every_job_has_a_timeout() -> None:
    workflow = _workflow_yaml()
    for job_name, job in workflow["jobs"].items():
        assert "timeout-minutes" in job, f"job {job_name!r} has no timeout-minutes"


def test_no_github_hosted_only_guard_remains() -> None:
    text = _workflow_text()
    assert "github-hosted" not in text


def test_python_setup_caches_poetry() -> None:
    workflow = _workflow_yaml()
    steps = workflow["jobs"]["test"]["steps"]
    setup_python = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    assert setup_python.get("with", {}).get("cache") == "poetry"
    # Poetry must already be on PATH by the time setup-python's cache step
    # runs, or there's nothing for it to key the cache against.
    setup_index = steps.index(setup_python)
    poetry_install_index = next(i for i, s in enumerate(steps) if "install poetry" in s.get("name", "").lower())
    assert poetry_install_index < setup_index, "Poetry must be installed before actions/setup-python's cache step runs"


def test_postgres_container_is_torn_down_unconditionally() -> None:
    workflow = _workflow_yaml()
    steps = workflow["jobs"]["test"]["steps"]
    teardown = [s for s in steps if s.get("if") == "always()" and "docker rm" in s.get("run", "")]
    assert teardown, "no always()-guarded `docker rm` teardown step for the Postgres container"


def test_postgres_port_is_dynamic_not_pinned_to_dev_db_port() -> None:
    text = _workflow_text()
    assert "5433" not in text, "ci.yml must not bind the test Postgres to the documented dev-DB port 5433"
