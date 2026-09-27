"""Invariants of ci.yml's `image` job, the one CI job that builds what Railway
deploys. Why the job exists and why it is hosted and unfiltered is stated in
its comment in ci.yml."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from tests.unit.test_ci_workflow_gates import ROOT, _workflow_yaml

RAILWAY_CONFIG = ROOT / "railway.json"


def _image_job() -> dict:
    builders = [
        job
        for job in _workflow_yaml()["jobs"].values()
        if any(step.get("uses", "").startswith("docker/build-push-action@") for step in job.get("steps", []))
    ]
    assert len(builders) == 1, f"expected exactly one job building the production image, found {len(builders)}"
    return builders[0]


def _build_step(job: dict) -> tuple[int, dict]:
    return next(
        (i, step) for i, step in enumerate(job["steps"]) if step.get("uses", "").startswith("docker/build-push-action@")
    )


def _built_tag(build: dict) -> str:
    tags = [t.strip() for t in str(build["with"]["tags"]).replace(",", "\n").splitlines() if t.strip()]
    assert len(tags) == 1, f"the smoke test must run the one image that was built, got tags {tags}"
    return tags[0]


def _docker_runs_after_build(job: dict) -> list[list[str]]:
    """The in-container argv of every `docker run` of the built tag after the build step."""
    build_index, build = _build_step(job)
    tag = _built_tag(build)
    commands = []
    for step in job["steps"][build_index + 1 :]:
        script = step.get("run", "").replace("\\\n", " ")
        for line in script.splitlines():
            argv = shlex.split(line, comments=True)
            if argv[:2] == ["docker", "run"] and tag in argv:
                commands.append(argv[argv.index(tag) + 1 :])
    return commands


def test_image_job_is_hosted_and_read_only() -> None:
    job = _image_job()
    assert job["runs-on"] == "ubuntu-latest"
    assert job.get("permissions") == {"contents": "read"}


def test_image_job_runs_on_every_pull_request() -> None:
    job = _image_job()
    assert "if" not in job, "the image job must not be conditional"


def test_image_job_builds_the_dockerfile_railway_deploys_without_pushing() -> None:
    job = _image_job()
    _, build = _build_step(job)
    options = build["with"]

    assert options.get("context") == ".", "Railway builds from the repository root"
    railway_dockerfile = json.loads(RAILWAY_CONFIG.read_text())["build"]["dockerfilePath"]
    built = Path(options.get("file", "Dockerfile"))
    assert built == Path(railway_dockerfile), f"CI builds {built}, Railway deploys {railway_dockerfile}"

    assert options.get("push", False) is False, "the CI build must never push the image anywhere"
    assert options.get("load") is True, "without load the smoke test has no local image to run"


def test_image_is_smoke_tested_after_it_is_built() -> None:
    """A successful build proves only that the layers assemble, not that the
    app loads from them or that the frontend stage's output shipped."""
    job = _image_job()
    commands = [" ".join(argv) for argv in _docker_runs_after_build(job)]
    assert any(command == "python -c import api.main" for command in commands), (
        f"no `docker run` of the built image imports api.main: {commands}"
    )
    assert any("api/static/index.html" in command for command in commands), (
        f"no `docker run` of the built image checks the bundled SPA: {commands}"
    )
