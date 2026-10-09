"""CI hardening invariants for `.github/workflows/ci.yml`.

The docs (README, docs/features) call `npm run lint:i18n`
and `npm run lint:i18n-strings` mandatory gates, but nothing actually ran them
in CI, so a key-parity or exact-string regression could merge unnoticed. The
workflow also lacked the baseline hardening (concurrency de-dup, least-
privilege permissions, job timeouts) every other workflow in this repo has,
and carried a "free disk space" step that can only ever fire on a
GitHub-hosted runner this repo doesn't use.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
BACKEND_JOBS = ("backend-static", "test", "coverage")
SETUP_BACKEND = ROOT / ".github" / "actions" / "setup-backend" / "action.yml"
BACKEND_RUNNER = "${{ fromJSON(vars.CI_BACKEND_RUNNER || '\"ubuntu-latest\"') }}"


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


@pytest.mark.parametrize("job_name", BACKEND_JOBS)
def test_every_backend_job_sets_up_through_the_shared_action(job_name: str) -> None:
    """One definition of the toolchain, so the ordering below holds for every
    backend job rather than for whichever copy was last edited."""
    steps = _workflow_yaml()["jobs"][job_name]["steps"]
    assert any(step.get("uses") == "./.github/actions/setup-backend" for step in steps), (
        f"{job_name} no longer sets up through .github/actions/setup-backend"
    )


def test_poetry_is_installed_after_setup_python() -> None:
    """The VPS runner supplies no system `pip`, so Poetry can only be
    installed once setup-python has put an interpreter on PATH, and every
    backend job can be pointed at the VPS.

    This forecloses `cache: poetry`, which requires the opposite order —
    Poetry present before setup-python runs. Inverting the steps to gain the
    cache fails the job outright with `pip: command not found`, so the order
    is pinned here rather than left to look like an arbitrary preference.
    """
    steps = yaml.safe_load(SETUP_BACKEND.read_text())["runs"]["steps"]
    setup_python = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    setup_index = steps.index(setup_python)
    poetry_index = next(i for i, s in enumerate(steps) if "install poetry" in s.get("name", "").lower())
    assert setup_index < poetry_index, "setup-python must run first; without it there is no pip to install Poetry with"
    assert setup_python.get("with", {}).get("cache") != "poetry", (
        "cache: poetry needs Poetry installed before this step, which the VPS runner cannot do"
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


ACTION_PATH = ROOT / ".github" / "actions" / "start-test-postgres" / "action.yml"


def _action_yaml() -> dict:
    return yaml.load(ACTION_PATH.read_text(), Loader=_NoDuplicateKeys)


def _action_steps() -> list[dict]:
    return _action_yaml()["runs"]["steps"]


def _postgres_consumers() -> list:
    workflows = (ROOT / ".github" / "workflows").glob("*.yml")
    return [w for w in workflows if "start-test-postgres" in w.read_text()]


_SCOPE_EXPRESSION = "github.event.pull_request.number || github.ref"


def test_container_scope_matches_the_concurrency_group() -> None:
    """The container must be scoped to the same unit concurrency serializes.

    One runner is one Docker daemon, and the action force-removes whatever
    holds the name before starting. Two runs that can overlap must therefore
    hold different names, and the set of runs that cannot overlap is exactly
    what the concurrency group defines. Deriving both from one expression is
    what keeps the two from drifting into a case where they disagree.
    """
    concurrency_group = _workflow_yaml()["concurrency"]["group"]
    assert _SCOPE_EXPRESSION in concurrency_group, (
        "ci.yml's concurrency group no longer uses the expression the container name is derived from"
    )
    resolve = next(s for s in _action_steps() if s.get("id") == "resolve")
    assert _SCOPE_EXPRESSION in resolve["run"], (
        "the container name is not scoped to the pull request / ref the concurrency group uses"
    )


def test_leftover_from_the_same_scope_is_cleared_before_starting() -> None:
    """Scoping per pull request is only safe because of this step.

    `cancel-in-progress` abandons a container on every re-push, so the next
    run of that pull request arrives at a name that is already taken. Without
    this, `docker run` fails on the name and the whole design regresses to
    being worse than a fixed name.
    """
    steps = _action_steps()
    run_index = next(i for i, s in enumerate(steps) if "docker run" in s.get("run", ""))
    removals = [
        i
        for i, s in enumerate(steps)
        if "docker rm" in s.get("run", "") and "steps.resolve.outputs.container-name" in s.get("run", "")
    ]
    assert removals, "nothing clears a leftover container for this scope before `docker run`"
    assert min(removals) < run_index, "the leftover is cleared after the container is started, which is too late"


def test_image_tag_does_not_follow_the_container_name() -> None:
    """A scoped tag would leave one dangling image tag per pull request.

    The container name varies; the tag must not follow it, or a persistent
    runner accumulates tags forever. Every caller builds the same db/ context,
    so one shared tag is also one shared layer cache.
    """
    build = next(s for s in _action_steps() if "docker build" in s.get("run", ""))
    assert "inputs.container-name" not in build["run"], "the image tag must not follow the container name"
    assert "steps.resolve" not in build["run"], "the image tag must not be scoped per pull request"


def test_action_outputs_wire_to_their_own_steps() -> None:
    """Both outputs are one expression each, and they are easy to cross-wire.

    Pointing `port` at the name step yields a hostless URL that fails far
    from here, at whatever step first opens a connection.
    """
    outputs = _action_yaml()["outputs"]
    assert outputs["port"]["value"] == "${{ steps.publish.outputs.port }}"
    assert outputs["container-name"]["value"] == "${{ steps.resolve.outputs.container-name }}"


def test_no_caller_removes_the_container_by_the_prefix_it_passed_in() -> None:
    """The prefix is no longer a container that exists.

    A teardown still naming it removes nothing and reports success, so the
    leak is silent — the container survives until the action's reaper
    collects it hours later, holding a volume the whole time.
    """
    for workflow in _postgres_consumers():
        text = workflow.read_text()
        prefixes = re.findall(r"container-name:\s*(\S+)", text)
        for prefix in prefixes:
            assert f"docker rm -f -v {prefix}" not in text, (
                f"{workflow.name} tears down the bare prefix {prefix!r}; use the action's container-name output"
            )


def test_every_caller_removes_its_container() -> None:
    """A run-unique name means no later run reclaims it by name.

    The action's label reaper is the backstop, but it waits out a three-hour
    cutoff; without an explicit teardown a weekly workflow would hold a
    volume for that long after every run.
    """
    for workflow in _postgres_consumers():
        text = workflow.read_text()
        assert "outputs.container-name" in text, (
            f"{workflow.name} starts Postgres but never removes it by the action's resolved name"
        )


def test_every_container_removal_takes_its_volume_with_it() -> None:
    """`docker rm` without `-v` keeps the container's anonymous volume.

    The postgres image declares VOLUME /var/lib/postgresql/data, so every run
    creates one. A removal that drops the container but not the volume leaks
    roughly a quarter gigabyte per run, invisibly — nothing fails, the disk
    just fills. This repo's runner had accumulated 47 of them, 10.4 GB.
    """
    sources = [ACTION_PATH, *_postgres_consumers()]
    for path in sources:
        for line in path.read_text().splitlines():
            if "docker rm" not in line:
                continue
            assert "-v" in line.split("docker rm", 1)[1], (
                f"{path.name}: `docker rm` without -v leaks a volume: {line.strip()}"
            )


def test_frontend_job_stays_off_the_self_hosted_runner() -> None:
    """It has no reason to be there and one strong reason not to be.

    The job needs no Docker, database or ClickHouse, so it stays hosted even
    when the backend jobs are pointed at the VPS: there it would only queue
    behind them on the single runner, on a box whose 3.8 GiB of RAM does not
    fit two jobs at once.
    """
    jobs = _workflow_yaml()["jobs"]
    assert jobs["frontend"]["runs-on"] == "ubuntu-latest"

    frontend_text = yaml.safe_dump(jobs["frontend"])
    for needs_the_vps in ("docker", "localhost", "services"):
        assert needs_the_vps not in frontend_text, (
            f"the frontend job now references {needs_the_vps!r}; re-check whether it can still be hosted"
        )


def test_backend_jobs_run_hosted_unless_one_variable_points_both_at_the_vps() -> None:
    """Hosted while `vars.CI_BACKEND_RUNNER` is unset, since standard hosted
    runners cost nothing for a public repository. One variable moves both
    backend jobs, so the static checks and the suite never land on different
    machines by accident."""
    jobs = _workflow_yaml()["jobs"]
    for job_name in BACKEND_JOBS:
        assert jobs[job_name]["runs-on"] == BACKEND_RUNNER, f"{job_name} no longer follows vars.CI_BACKEND_RUNNER"


def test_every_shard_the_matrix_starts_keeps_its_own_share() -> None:
    """Each shard keeps the files tests/sharding.py assigns to
    `CI_SHARD_INDEX` of `CI_SHARD_COUNT`. The count has to be the matrix's
    own size: a smaller one leaves shards out of range, and a larger one
    leaves shares that no job runs, which would read as a pass."""
    test = _workflow_yaml()["jobs"]["test"]
    assert test["strategy"]["fail-fast"] is False, "one shard's failure must not cancel the others' report"
    run = next(s for s in test["steps"] if s.get("name") == "Run tests")
    assert run["env"]["CI_SHARD_INDEX"] == "${{ matrix.shard }}"
    assert run["env"]["CI_SHARD_COUNT"] == "${{ strategy.job-total }}"


def test_static_checks_run_once_over_the_whole_tree() -> None:
    """ruff and mypy run in `backend-static` and nowhere in the sharded job,
    where they would run once per shard over the same tree."""
    jobs = _workflow_yaml()["jobs"]
    static = " ".join(step.get("run", "") for step in jobs["backend-static"]["steps"])
    for check in ("ruff check .", "ruff format --check .", "mypy"):
        assert check in static, f"backend-static no longer runs `{check}`"
    sharded = " ".join(step.get("run", "") for step in jobs["test"]["steps"])
    for tool in ("ruff", "mypy"):
        assert tool not in sharded, f"the sharded test job runs {tool} again, once per shard"


def test_main_reports_coverage_combined_from_every_shard() -> None:
    """A shard measures only its share of the suite, so its own figure
    understates what the suite covers. Each shard keeps its data under a name
    of its own, and one job after all of them combines it."""
    jobs = _workflow_yaml()["jobs"]
    run = next(s for s in jobs["test"]["steps"] if s.get("name") == "Run tests")
    assert run["env"]["COVERAGE_FILE"] == ".coverage.shard-${{ matrix.shard }}"
    upload = next(s for s in jobs["test"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["with"]["path"] == ".coverage.shard-${{ matrix.shard }}"
    assert upload["with"]["include-hidden-files"] is True, "a dot-file is skipped by the upload without this"
    coverage = jobs["coverage"]
    assert coverage["needs"] == "test"
    assert coverage["if"] == upload["if"] == "github.event_name == 'push'"
    assert "coverage combine .coverage.shard-*" in " ".join(s.get("run", "") for s in coverage["steps"])


def _names_self_hosted(runs_on: object) -> bool:
    """Whether a job's `runs-on` names a self-hosted label itself, rather
    than through an `${{ }}` expression a maintainer's variable resolves."""
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    return any("self-hosted" in str(label) and not str(label).startswith("${{") for label in labels)


@pytest.mark.parametrize(
    ("runs_on", "named"),
    [
        (["self-hosted", "vps"], True),
        ("self-hosted", True),
        (["ubuntu-latest", "self-hosted"], True),
        ("ubuntu-latest", False),
        ("${{ fromJSON(vars.CI_BACKEND_RUNNER || '\"ubuntu-latest\"') }}", False),
    ],
)
def test_a_self_hosted_label_is_told_from_the_variable_that_may_resolve_to_one(runs_on: object, named: bool) -> None:
    assert _names_self_hosted(runs_on) is named


def test_no_workflow_names_a_self_hosted_runner_outright() -> None:
    """No runner is registered for this public repository: a self-hosted
    runner there can be handed code from pull requests outside it. The one
    way back is `vars.CI_BACKEND_RUNNER`, set by a maintainer after
    registering one, so no workflow may name a self-hosted label itself."""
    offenders = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        doc = yaml.load(workflow.read_text(), _NoDuplicateKeys)
        for job_name, job in doc["jobs"].items():
            if _names_self_hosted(job.get("runs-on", "")):
                offenders.append(f"{workflow.name} / {job_name}: {job['runs-on']}")
    assert not offenders, "jobs pinned to a self-hosted runner: " + "; ".join(offenders)


def test_reaper_cutoff_clears_the_job_timeout_without_dawdling() -> None:
    """Both directions are failures.

    Below the job timeout, the reaper deletes a running job's database. Far
    above it, a hard-cancelled job's container — the case the per-scope
    cleanup cannot reach, because that job never runs its teardown — holds
    its volume long after it stopped being useful.

    Only the number is checked here; the reaper's behaviour is executed in
    tests/unit/test_ci_reap_stale.py against a shimmed Docker, because the
    predecessor of this check asserted the presence of a `until=` filter
    that `docker ps` does not accept and passed for months of nothing being
    reaped.
    """
    reap = next(s for s in _action_steps() if "reap-stale.sh" in s.get("run", ""))
    seconds = re.search(r"reap-stale\.sh\S*\"?\s+\S+\s+(\d+)", reap["run"])
    assert seconds, f"could not read the reaper's cutoff from: {reap['run']!r}"
    cutoff_minutes = int(seconds.group(1)) / 60

    timeouts = [job["timeout-minutes"] for job in _workflow_yaml()["jobs"].values()]
    longest_job = max(timeouts)
    assert cutoff_minutes > longest_job, f"cutoff {cutoff_minutes}min can reap a live job (timeout {longest_job}min)"
    assert cutoff_minutes <= longest_job * 2, (
        f"cutoff {cutoff_minutes}min holds abandoned volumes far longer than a job can possibly run"
    )


def _run_shell(doc: dict, job: dict, step: dict) -> str | None:
    """The shell a step's `run` executes under, resolving the step/job/workflow defaults."""
    workflow_default = ((doc.get("defaults") or {}).get("run") or {}).get("shell")
    job_default = ((job.get("defaults") or {}).get("run") or {}).get("shell")
    return step.get("shell") or job_default or workflow_default


def test_every_step_that_pipes_through_tee_declares_bash_so_the_pipe_fails_loud() -> None:
    """GitHub's default `run` shell is `bash -e {0}` without `pipefail`: a
    `pytest | tee log` step exits with tee's 0 whatever pytest returned, and
    the job stays green over a failing suite. `shell: bash` switches to
    `bash --noprofile --norc -eo pipefail {0}`.

    `shell: bash`, on the step or as a job/workflow default, is the one spelling
    accepted. An inline `set -o pipefail` would work as well, but recognising it
    means reading the script as shell, which this check deliberately does not."""
    checked = 0
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        doc = yaml.load(workflow.read_text(), _NoDuplicateKeys)
        for job_name, job in doc["jobs"].items():
            for step in job.get("steps", []):
                if "| tee" not in step.get("run", ""):
                    continue
                checked += 1
                assert _run_shell(doc, job, step) == "bash", (
                    f"{workflow.name} / {job_name} / {step.get('name', step['run'][:40])!r} pipes through tee "
                    "without `shell: bash` (the one spelling this check accepts), so a failing left-hand command "
                    "cannot fail the step"
                )
    assert checked >= 1, "expected at least one `| tee` step (nightly-extended.yml); the probe found none"
