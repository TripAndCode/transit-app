"""Behaviour of the test-Postgres container-name derivation.

The name decides whether two CI jobs sharing one Docker daemon destroy each
other's database, so the properties below are executed rather than read off
the action's YAML.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "actions" / "start-test-postgres" / "container-name.sh"

# What Docker will accept as a container name.
DOCKER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

PREFIX = "transit-ci-db"


def _name(scope: str, prefix: str = PREFIX) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT), prefix, scope],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_same_scope_is_the_same_container():
    """Re-running one pull request must reclaim its own container.

    `cancel-in-progress` abandons a container on every re-push. A name that
    changed between runs of the same pull request would leave every one of
    them behind.
    """
    assert _name("536") == _name("536")


def test_different_scopes_do_not_share_a_container():
    """Two pull requests can run at once; that is the point of the change."""
    assert _name("536") != _name("537")
    assert _name("refs/heads/a") != _name("refs/heads/b")


def test_different_callers_do_not_share_a_container():
    """Nightly and the weekly eval both run on main's ref."""
    assert _name("refs/heads/main", "transit-nightly-db") != _name("refs/heads/main", "transit-eval-db")


def test_a_ref_with_slashes_is_a_valid_docker_name():
    """`refs/heads/fix/a-b` is the common case and is not a legal name."""
    name = _name("refs/heads/fix/routes-operations-rename")
    assert DOCKER_NAME.match(name), name
    assert "/" not in name


def test_every_plausible_scope_yields_a_valid_docker_name():
    scopes = [
        "536",
        "refs/heads/main",
        "refs/pull/536/merge",
        "refs/heads/Feature/UPPER_Case",
        "refs/heads/déjà-vu",
        "refs/tags/v1.2.3",
        "refs/heads/" + "x" * 200,
        "refs/heads/---",
        "",
    ]
    for scope in scopes:
        name = _name(scope)
        assert DOCKER_NAME.match(name), f"{scope!r} produced an invalid name: {name!r}"


def test_an_empty_scope_does_not_collapse_every_caller_onto_one_name():
    """A blank scope is the shape a missing context would take. Falling back
    to the bare prefix would silently reintroduce the shared name this whole
    change removes, so it gets its own suffix instead."""
    name = _name("")
    assert name != PREFIX
    assert name.startswith(PREFIX + "-")


def test_long_refs_are_truncated_but_still_distinguish_branches():
    a = _name("refs/heads/" + "a" * 120 + "-one")
    b = _name("refs/heads/" + "a" * 120 + "-two")
    assert a != b, "truncation dropped the part that tells two long branches apart"
    assert len(a) < 80, f"name is unreadable in `docker ps`: {len(a)} chars"


def test_prefix_is_required():
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode != 0
