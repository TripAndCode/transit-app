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
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

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


# A ping URL is read on whichever host runs its job, so `.env.example` lists
# each one, commented where the app itself never reads it. The example env
# files only document names, so they are not readers. `HC_PING_URL_` is the
# prefix `deploy/vps/run-with-ping.sh` completes with the job name each unit's
# ExecStart passes it.
_PING_URL = re.compile(r"\b[A-Z][A-Z0-9_]*PING_URL[A-Z0-9_]*\b")
_PING_JOB = re.compile(r"^ExecStart=\S*run-with-ping\.sh\s+([A-Z][A-Z0-9_]*)", re.M)
_LISTED_PING_URL = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*PING_URL[A-Z0-9_]*)=", re.M)
_NOT_A_PING_VARIABLE = {"HC_PING_URL_"}


def test_env_example_lists_every_ping_url_the_code_reads():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    code = [
        p
        for p in tracked
        if not p.startswith(("tests/", "docs/", "frontend/"))
        and "/tests/" not in p
        and not p.endswith((".md", "example"))
    ]
    texts = [(ROOT / p).read_text(errors="ignore") for p in code if (ROOT / p).is_file()]
    read = {name for text in texts for name in _PING_URL.findall(text)} - _NOT_A_PING_VARIABLE
    read |= {"HC_PING_URL_" + job for text in texts for job in _PING_JOB.findall(text)}
    listed = set(_LISTED_PING_URL.findall((ROOT / ".env.example").read_text()))
    assert {"POLLER_PING_URL", "HC_PING_URL_SYNC"} <= read
    assert read <= listed, f".env.example does not list {sorted(read - listed)}"


_DEV_SERVICES = {"db", "clickhouse"}


def _services(text: str) -> dict:
    """The parsed services, `<<` merges resolved."""
    return yaml.safe_load(text)["services"]


def _builds_the_app(svc: dict) -> bool:
    """Whether *svc* runs the app image: `build` is the repository root, in
    either its short or its long form."""
    build = svc.get("build")
    return (build.get("context") if isinstance(build, dict) else build) == "."


def _standalone_violations(text: str) -> list[str]:
    """How any `standalone`-profile service could reach the dev stack: a
    database URL or ClickHouse host that is not the profile's own, the host's
    network, a dev volume, or a dependency outside the profile."""
    services = _services(text)
    standalone = {name for name, svc in services.items() if "standalone" in (svc.get("profiles") or [])}
    found = []
    for name in sorted(standalone):
        svc = services[name]
        env = svc.get("environment") or {}
        if _builds_the_app(svc):
            if urlsplit(str(env.get("DATABASE_URL", ""))).hostname != "standalone-db":
                found.append(f"{name}: DATABASE_URL is not on standalone-db")
            if env.get("CLICKHOUSE_HOST") != "standalone-clickhouse":
                found.append(f"{name}: CLICKHOUSE_HOST is not standalone-clickhouse")
        if "network_mode" in svc:
            found.append(f"{name}: sets network_mode")
        for volume in svc.get("volumes") or []:
            source = volume.split(":", 1)[0] if isinstance(volume, str) else volume.get("source", "")
            if source.startswith("transit_"):
                found.append(f"{name}: mounts {source}")
        for dependency in svc.get("depends_on") or {}:
            if dependency not in standalone:
                found.append(f"{name}: depends on {dependency}")
    return found


def test_the_dev_services_keep_no_profile():
    services = _services(_COMPOSE_TEXT)
    for name in _DEV_SERVICES:
        assert "profiles" not in services[name], f"{name} must stay in the default profile"


def test_the_standalone_stack_never_reaches_the_dev_databases():
    services = _services(_COMPOSE_TEXT)
    assert {"app", "poller", "migrate", "standalone-db", "standalone-clickhouse"} <= {
        name for name, svc in services.items() if "standalone" in (svc.get("profiles") or [])
    }
    assert all(_builds_the_app(services[name]) for name in ("app", "poller", "migrate"))
    assert _standalone_violations(_COMPOSE_TEXT) == []


def test_the_standalone_databases_publish_no_port():
    services = _services(_COMPOSE_TEXT)
    for name in ("standalone-db", "standalone-clickhouse"):
        assert "ports" not in services[name], f"{name} must not publish a host port"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("CLICKHOUSE_HOST: standalone-clickhouse", 'CLICKHOUSE_HOST: "clickhouse"'),
        ("@standalone-db:5432", "@db:5432"),
        ("@standalone-db:5432", "@host.docker.internal:5433"),
        ("    stop_grace_period: 45s", "    stop_grace_period: 45s\n    network_mode: host"),
        ("      - standalone_pgdata:/var/lib/postgresql/data", "      - transit_pgdata:/var/lib/postgresql/data"),
        ("    migrate:\n      condition", "    db:\n      condition: service_healthy\n    migrate:\n      condition"),
    ],
    ids=["quoted-dev-clickhouse", "dev-db-url", "host-gateway-db", "host-network", "dev-volume", "dev-dependency"],
)
def test_each_way_to_reach_the_dev_stack_is_caught(old, new):
    assert old in _COMPOSE_TEXT
    assert _standalone_violations(_COMPOSE_TEXT.replace(old, new, 1))


_COMPOSE_TEXT = (ROOT / "compose.yml").read_text()
