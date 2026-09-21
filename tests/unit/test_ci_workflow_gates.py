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


class _NoDuplicateKeys(yaml.SafeLoader):
    """SafeLoader that refuses a duplicate mapping key.

    `yaml.safe_load` silently keeps the last of a duplicated key, which is
    exactly wrong for a workflow file: GitHub's own parser rejects a duplicate
    top-level key and refuses to run the workflow at all, so a test that loads
    permissively asserts against a document GitHub would never execute — and
    passes while CI is entirely broken.
    """


def _no_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def _workflow_yaml() -> dict:
    return yaml.load(_workflow_text(), _NoDuplicateKeys)


def test_workflow_has_no_duplicate_keys() -> None:
    """A duplicated key makes the whole workflow invalid to GitHub, so nothing
    runs — a worse outcome than any single gate being wrong. Asserted on its
    own rather than left implicit in the other tests' parsing, so the failure
    names the real problem."""
    _workflow_yaml()


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


def test_concurrency_supersedes_pull_request_runs_but_never_a_main_run() -> None:
    """One in-flight run per pull request, and a `main` run is never cancelled.

    Both halves matter on a single runner: superseding a PR's previous run is
    what keeps the current head from queueing behind every earlier head, while
    a `main` run records what that commit did, so cancelling it would drop the
    recorded status for a commit that is already merged. An unconditional
    `cancel-in-progress: true` satisfies the first and breaks the second.
    """
    workflow = _workflow_yaml()
    concurrency = workflow.get("concurrency")
    assert concurrency is not None, "workflow is missing a top-level `concurrency` block"
    assert "github.event.pull_request.number" in concurrency["group"], (
        "the group must be keyed per pull request, or two PRs would supersede each other"
    )
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}", (
        "cancellation must be conditional on the event; `true` would cancel a main run too"
    )


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


def test_poetry_is_installed_after_setup_python() -> None:
    """The runner supplies no system `pip`, so Poetry can only be installed
    once setup-python has put an interpreter on PATH.

    This forecloses `cache: poetry`, which requires the opposite order —
    Poetry present before setup-python runs. Inverting the steps to gain the
    cache fails the job outright with `pip: command not found`, so the order
    is pinned here rather than left to look like an arbitrary preference.
    """
    workflow = _workflow_yaml()
    steps = workflow["jobs"]["test"]["steps"]
    setup_python = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    setup_index = steps.index(setup_python)
    poetry_index = next(i for i, s in enumerate(steps) if "install poetry" in s.get("name", "").lower())
    assert setup_index < poetry_index, "setup-python must run first; without it there is no pip to install Poetry with"
    assert setup_python.get("with", {}).get("cache") != "poetry", (
        "cache: poetry needs Poetry installed before this step, which this runner cannot do"
    )


def test_postgres_container_is_torn_down_unconditionally() -> None:
    workflow = _workflow_yaml()
    steps = workflow["jobs"]["test"]["steps"]
    teardown = [s for s in steps if s.get("if") == "always()" and "docker rm" in s.get("run", "")]
    assert teardown, "no always()-guarded `docker rm` teardown step for the Postgres container"


def test_postgres_port_is_dynamic_not_pinned_to_dev_db_port() -> None:
    text = _workflow_text()
    assert "5433" not in text, "ci.yml must not bind the test Postgres to the documented dev-DB port 5433"


def test_no_workflow_hardcodes_the_old_fixed_postgres_port() -> None:
    """Every consumer of the start-test-postgres action must read its port output.

    The action publishes on a Docker-assigned port, so a workflow still naming
    the old fixed one connects to nothing. That is easy to miss because a
    second consumer lives in a different file from the one being edited, and
    the failure surfaces only when that workflow next runs -- for the weekly
    eval, on a schedule nobody is watching.
    """
    workflows = (ROOT / ".github" / "workflows").glob("*.yml")
    consumers = [w for w in workflows if "start-test-postgres" in w.read_text()]
    assert consumers, "expected at least one workflow to use the composite action"

    stale = [w.name for w in consumers if "localhost:54" + "33" in w.read_text()]
    assert not stale, f"these still point at the old fixed port instead of the action's output: {stale}"

    for w in consumers:
        text = w.read_text()
        assert "outputs.port" in text, f"{w.name} starts Postgres but never reads the allocated port"
