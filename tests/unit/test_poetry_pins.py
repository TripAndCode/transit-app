"""Every command that installs Poetry pins one exact version inside
`pyproject.toml`'s `requires-poetry`, so CI, the deploy image and a local
checkout resolve `poetry.lock` the same way.

A pin that is not `==`, or a `pip`/`pipx` install with no pin at all, fails
here rather than being skipped: either one lets a runner pick up whatever
Poetry is newest that day. Only executed command text in tracked files is
scanned: workflow `run:` scripts, every Dockerfile's `RUN`, Makefile recipes
and every shell script, each with backslash continuations joined first. Step
names and comments that mention Poetry cannot trip it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

import tomllib
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]

_POETRY = r"\bpoetry(?![-\w])"
_VERSIONED = re.compile(_POETRY + r"\s*(?P<op>[=<>~!]=?|@)\s*(?P<version>\d[^\s\"'\\]*)", re.IGNORECASE)
_UNPINNED_INSTALL = re.compile(r"\b(?:pip|pipx)\s+install\b[^\n]*" + _POETRY + r"(?!\s*[=<>~!@])", re.IGNORECASE)


def _logical_lines(text: str) -> list[str]:
    """Non-comment lines, with each backslash-continued run joined into one."""
    lines, pending = [], ""
    for line in text.splitlines():
        if not pending and line.lstrip().startswith("#"):
            continue
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def _workflow_commands(text: str) -> list[str]:
    workflow = yaml.safe_load(text) or {}
    return [
        line
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
        for line in _logical_lines(step.get("run", ""))
    ]


def _tracked(*pathspecs: str) -> list[PurePosixPath]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspecs], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return sorted(PurePosixPath(path) for path in listed.split("\0") if path)


def _commands() -> dict[str, list[str]]:
    sites: dict[str, list[str]] = {}
    for path in _tracked(".github/workflows/*.yml", ".github/workflows/*.yaml", "*Dockerfile*", "*Makefile", "*.sh"):
        text = (ROOT / path).read_text()
        if path.parent == PurePosixPath(".github/workflows"):
            sites[str(path)] = _workflow_commands(text)
        elif path.name.startswith("Dockerfile"):
            sites[str(path)] = [line for line in _logical_lines(text) if line.lstrip().upper().startswith("RUN ")]
        elif path.name == "Makefile":
            sites[str(path)] = [line for line in _logical_lines(text) if line.startswith("\t")]
        elif path.suffix == ".sh":
            sites[str(path)] = _logical_lines(text)
    return sites


def _pins() -> list[tuple[str, str, str]]:
    return [
        (site, match["op"], match["version"])
        for site, commands in _commands().items()
        for command in commands
        for match in _VERSIONED.finditer(command)
    ]


def _requires_poetry() -> SpecifierSet:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return SpecifierSet(pyproject["tool"]["poetry"]["requires-poetry"])


def test_every_poetry_pin_is_exact_and_identical():
    pins = _pins()
    assert pins, "found no `poetry==` pin in any install command"
    loose = [(site, op, version) for site, op, version in pins if op != "=="]
    assert not loose, f"Poetry pinned with something other than `==`: {loose}"
    versions = {version for _, _, version in pins}
    assert len(versions) == 1, f"Poetry pins disagree: {pins}"


def test_pinned_poetry_satisfies_requires_poetry():
    (version,) = {version for _, _, version in _pins()}
    assert Version(version) in _requires_poetry(), (
        f"pinned Poetry {version} is outside requires-poetry {_requires_poetry()}"
    )


def test_no_poetry_install_without_a_pin():
    unpinned = [
        (site, command.strip())
        for site, commands in _commands().items()
        for command in commands
        if _UNPINNED_INSTALL.search(command)
    ]
    assert not unpinned, f"Poetry installed without a version pin: {unpinned}"


def test_continued_lines_are_scanned_as_one_command():
    joined = _logical_lines("pip install \\\n  poetry\n# pip install poetry\n")
    assert [line for line in joined if _UNPINNED_INSTALL.search(line)] == ["pip install    poetry"]
    pinned = _VERSIONED.search(_logical_lines("pip install \\\n  poetry==2.5.1\n")[0])
    assert pinned is not None and pinned["version"] == "2.5.1"
