"""Behaviour of the abandoned-container reaper.

Driven through a shimmed `docker` rather than asserted against the script's
text. The bug this replaces was a `docker ps --filter until=...` call — a
filter `docker ps` does not have, which fails the daemon call outright. A
test that grepped the script for `until=` passed the whole time; only
executing it against something that answers like Docker catches that class.
"""

from __future__ import annotations

import subprocess
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "actions" / "start-test-postgres" / "reap-stale.sh"

LABEL = "transit-test-postgres"
ONE_HOUR = 3600


def _iso(ago_seconds: int) -> str:
    """Docker's own `.Created` format: RFC3339 with nanoseconds."""
    moment = datetime.now(UTC) - timedelta(seconds=ago_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def _fake_docker(tmp_path: Path, containers: dict[str, str]) -> Path:
    """A `docker` that answers ps/inspect from a fixture and logs removals.

    Anything the script asks for beyond those three verbs is an error, so a
    rewrite that reaches for an unsupported filter or subcommand fails here
    the way it would against the real daemon.
    """
    ids = " ".join(containers)
    cases = "\n".join(f'    {cid}) echo "{created}" ;;' for cid, created in containers.items())
    script = tmp_path / "docker"
    script.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env bash
        case "$1" in
          ps)
            # Only the label filter is supported, exactly like the real ps.
            for arg in "$@"; do
              case "$arg" in
                until=*|--filter=until=*)
                  echo "Error response from daemon: invalid filter 'until'" >&2; exit 1 ;;
              esac
            done
            echo "{ids}" | tr ' ' '\\n' | sed '/^$/d'
            ;;
          inspect)
            case "${{@: -1}}" in
        {cases}
              *) exit 1 ;;
            esac
            ;;
          rm)
            # Without -v the container's anonymous volume survives, which is
            # what filled this runner's disk. Refuse it here so the contract
            # is executed rather than grepped for.
            case " $* " in
              *" -v "*) ;;
              *) echo "docker rm without -v would orphan the volume" >&2; exit 1 ;;
            esac
            for arg in "$@"; do
              case "$arg" in
                rm|-f|-v) ;;
                *) echo "$arg" >> "$REMOVED_LOG" ;;
              esac
            done
            ;;
          *) echo "unexpected docker subcommand: $1" >&2; exit 1 ;;
        esac
        """)
    )
    script.chmod(0o755)
    return script


def _run(tmp_path: Path, containers: dict[str, str], max_age: int = ONE_HOUR):
    docker = _fake_docker(tmp_path, containers)
    removed_log = tmp_path / "removed.txt"
    removed_log.touch()
    result = subprocess.run(
        ["bash", str(SCRIPT), LABEL, str(max_age)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "DOCKER": str(docker), "REMOVED_LOG": str(removed_log)},
    )
    return result, removed_log.read_text().split()


def test_reaps_a_container_older_than_the_cutoff(tmp_path):
    result, removed = _run(tmp_path, {"abandoned1": _iso(4 * ONE_HOUR)})
    assert result.returncode == 0, result.stderr
    assert removed == ["abandoned1"]


def test_leaves_a_live_job_alone(tmp_path):
    """The negative control, and the one that matters most: reaping a
    container a concurrent job is using is the failure the whole per-scope
    naming design exists to prevent."""
    result, removed = _run(tmp_path, {"running1": _iso(120)})
    assert result.returncode == 0, result.stderr
    assert removed == []


def test_reaps_only_the_old_ones_when_both_are_present(tmp_path):
    result, removed = _run(
        tmp_path,
        {"fresh1": _iso(60), "abandoned1": _iso(9 * ONE_HOUR), "fresh2": _iso(30 * 60)},
    )
    assert result.returncode == 0, result.stderr
    assert removed == ["abandoned1"]


def test_removal_takes_the_volume(tmp_path):
    """The shim rejects `docker rm` without -v, so a removal that orphaned
    the volume would fail the sweep rather than pass it quietly.

    Enforced by executing the call, not by reading the script: this is the
    same defect that filled the runner's disk while a text check would have
    reported the flag present somewhere in the file.
    """
    result, removed = _run(tmp_path, {"abandoned1": _iso(4 * ONE_HOUR)})
    assert result.returncode == 0, result.stderr
    assert removed == ["abandoned1"], "the removal was rejected — see the shim's -v contract"


def test_succeeds_when_nothing_matches(tmp_path):
    result, removed = _run(tmp_path, {})
    assert result.returncode == 0, result.stderr
    assert removed == []


def test_an_uninspectable_container_does_not_abort_the_sweep(tmp_path):
    """A container removed between the ps and the inspect is a normal race
    on a shared daemon, not a reason to fail the job that is starting."""
    containers = {"vanished1": "", "abandoned1": _iso(5 * ONE_HOUR)}
    result, removed = _run(tmp_path, containers)
    assert result.returncode == 0, result.stderr
    assert removed == ["abandoned1"]


def test_requires_both_arguments(tmp_path):
    assert subprocess.run(["bash", str(SCRIPT)], capture_output=True).returncode != 0
    assert subprocess.run(["bash", str(SCRIPT), LABEL], capture_output=True).returncode != 0
