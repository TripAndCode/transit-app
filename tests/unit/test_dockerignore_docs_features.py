"""Static guards for the production container definition.

``api/routers/admin.py``'s architecture page reads ``docs/features/*.md`` at
runtime, but ``.dockerignore`` excludes ``docs`` wholesale — so without an
explicit re-include the directory never reaches the image and the page is
silently empty in production. These assertions parse the two files directly
(no Docker build available in this environment) so the invariant stays
enforced without needing an actual image build to notice a regression.
"""

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


def test_dockerfile_does_not_pin_node_20():
    text = (_REPO_ROOT / "Dockerfile").read_text()
    assert "node:20" not in text, "frontend build stage must match CI's Node version"
