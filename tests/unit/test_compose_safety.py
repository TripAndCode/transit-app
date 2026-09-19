"""Guards the local dev-infra safety properties `compose.yml` and
`.env.example` must keep (findings C6, E6, E7, E8 in the docs audit).

`compose.yml`'s Postgres port must stay loopback-bound like the ClickHouse
port next to it -- publishing 5433 on all interfaces exposes a
default-password Postgres to the whole LAN/VPN. `container_name` pins a
global Docker name, so two worktrees running `docker compose up` at once
collide on it; dropping it (and setting a top-level project `name` instead)
lets Compose derive per-project container names automatically.

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


def test_compose_declares_top_level_project_name():
    lines = _compose_lines()
    assert any(re.match(r"name:\s*\S+", line) for line in lines), (
        "compose.yml should declare a top-level `name:` so container/network names stay "
        "predictable without pinning them individually"
    )


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
