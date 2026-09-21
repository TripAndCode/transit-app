"""Static guards for the production container definition.

``api/routers/admin.py``'s architecture page reads ``docs/features/*.md`` at
runtime, but ``.dockerignore`` excludes ``docs`` wholesale — so without an
explicit re-include the directory never reaches the image and the page is
silently empty in production. These assertions parse the two files directly
(no Docker build available in this environment) so the invariant stays
enforced without needing an actual image build to notice a regression.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _dockerignore_lines() -> list[str]:
    text = (_REPO_ROOT / ".dockerignore").read_text()
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_dockerignore_reincludes_docs_features_after_excluding_docs():
    lines = _dockerignore_lines()
    docs_index = lines.index("docs")
    negation_index = lines.index("!docs/features/")
    assert negation_index > docs_index, (
        "!docs/features/ must come after the docs exclusion — Docker "
        "evaluates .dockerignore patterns in order, so a later negation "
        "re-includes what an earlier broader pattern excluded"
    )


def test_dockerfile_runs_as_non_root_user():
    text = (_REPO_ROOT / "Dockerfile").read_text()
    assert "USER " in text, "Dockerfile must switch to a non-root user before CMD"


def test_dockerfile_declares_a_healthcheck():
    text = (_REPO_ROOT / "Dockerfile").read_text()
    assert "HEALTHCHECK " in text


def test_dockerfile_node_major_matches_ci():
    """The image's frontend build stage must use the Node major CI builds with.

    Asserted against the workflow rather than against a specific version, so
    the two move together instead of the check rotting into a guard against
    one particular stale pin.
    """
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text()
    workflow = (_REPO_ROOT / ".github/workflows/ci.yml").read_text()

    image_major = re.search(r"^FROM node:(\d+)", dockerfile, re.MULTILINE)
    ci_major = re.search(r"""node-version:\s*["']?(\d+)""", workflow)
    assert image_major, "Dockerfile has no `FROM node:<major>` build stage"
    assert ci_major, "ci.yml no longer declares a node-version"
    assert image_major.group(1) == ci_major.group(1), (
        f"Dockerfile builds the frontend on Node {image_major.group(1)} but CI "
        f"uses Node {ci_major.group(1)}; the image would ship a bundle no CI run tested"
    )
