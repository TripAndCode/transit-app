"""Guards the local dev-infra safety properties `compose.yml` and
`.env.example` must keep.

`compose.yml`'s Postgres port must stay loopback-bound like the ClickHouse
port next to it -- publishing 5433 on all interfaces exposes a
default-password Postgres to the whole LAN/VPN. Neither `container_name` nor
a top-level `name:` may pin a fixed identity: both put every checkout in one
Docker project, so two worktrees running `docker compose up` share containers
and, worse, the same named data volumes. Left unpinned, Compose derives the
project from the checkout's own directory and the stacks stay separate.

Line-based rather than a YAML parse: PyYAML isn't a declared dependency of
this project (only pulled in transitively), and this file's structure is
simple enough that a targeted grep is more honest about what it checks than
parsing the whole document would be.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _compose_lines() -> list[str]:
    return (ROOT / "compose.yml").read_text().splitlines()


def test_postgres_port_is_loopback_bound():
    lines = _compose_lines()
    port_lines = [line for line in lines if re.search(r'"\d+:5432"|"[\d.]+:\d+:5432"', line)]
    assert port_lines, "expected a Postgres (5432) port mapping in compose.yml"
    for line in port_lines:
        assert "127.0.0.1:" in line, (
            f"Postgres port mapping must be loopback-bound like ClickHouse's, got: {line.strip()}"
        )


def test_compose_has_no_container_name():
    lines = _compose_lines()
    offending = [line for line in lines if re.match(r"\s*container_name:", line)]
    assert not offending, f"container_name pins a global name that blocks parallel worktree stacks: {offending}"


def test_compose_does_not_hardcode_a_project_name():
    """A static top-level `name:` defeats the isolation dropping `container_name` buys.

    Compose derives the project name from the checkout's directory unless the
    file pins one. Pinning it puts every worktree in the same project, so they
    share container names, the network, and — the damaging part — the named
    data volumes.
    """
    offending = [line for line in _compose_lines() if re.match(r"name:\s*\S+", line)]
    assert not offending, f"a fixed project name re-collides every worktree's stack and volumes: {offending}"


def test_env_example_has_no_bare_ipv4_literal():
    # 127.0.0.1 is a loopback placeholder like `localhost`, not an infra address
    # that could leak a real host -- excluded so this test targets the actual
    # risk (a real, reachable IP such as an OCI instance's public address).
    ipv4 = re.compile(r"\b(?!127\.0\.0\.1\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    offending = []
    for lineno, line in enumerate((ROOT / ".env.example").read_text().splitlines(), start=1):
        code_part = line.split("#", 1)[0]
        if ipv4.search(code_part):
            offending.append((lineno, line))
    assert not offending, f".env.example must not carry a real IP literal in a value: {offending}"
