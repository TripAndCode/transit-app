"""Every command that installs Poetry pins one exact version inside
`pyproject.toml`'s `requires-poetry`, so CI, the deploy image and a local
checkout resolve `poetry.lock` the same way.

A pin that is not `==`, or a `pip`/`pipx` install with no pin at all, fails
here rather than being skipped: either one lets a runner pick up whatever
Poetry is newest that day. Only executed command text is scanned (workflow
`run:` scripts, Dockerfile `RUN`, Makefile recipes, shell scripts), so step
names and comments that mention Poetry cannot trip it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]

_POETRY = r"\bpoetry(?![-\w])"
_VERSIONED = re.compile(_POETRY + r"\s*(?P<op>[=<>~!]=?|@)\s*(?P<version>\d[^\s\"'\\]*)", re.IGNORECASE)
_UNPINNED_INSTALL = re.compile(r"\b(?:pip|pipx)\s+install\b[^\n]*" + _POETRY + r"(?!\s*[=<>~!@])", re.IGNORECASE)


def _without_comments(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _workflow_commands(path: Path) -> list[str]:
    workflow = yaml.safe_load(path.read_text())
    return [
        line
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
        for line in _without_comments(step.get("run", ""))
    ]


def _dockerfile_commands(path: Path) -> list[str]:
    commands, current = [], None
    for line in _without_comments(path.read_text()):
        if current is None and line.lstrip().upper().startswith("RUN "):
            current = []
        if current is not None:
            current.append(line.rstrip().removesuffix("\\"))
            if not line.rstrip().endswith("\\"):
                commands.append(" ".join(current))
                current = None
    return commands


def _commands() -> dict[str, list[str]]:
    workflows = ROOT / ".github" / "workflows"
    sites: dict[str, list[str]] = {}
    for path in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
        sites[str(path.relative_to(ROOT))] = _workflow_commands(path)
    for path in sorted(ROOT.glob("Dockerfile*")):
        sites[str(path.relative_to(ROOT))] = _dockerfile_commands(path)
    makefile = ROOT / "Makefile"
    sites["Makefile"] = [line for line in _without_comments(makefile.read_text()) if line.startswith("\t")]
    for path in sorted((ROOT / "scripts").rglob("*.sh")):
        sites[str(path.relative_to(ROOT))] = _without_comments(path.read_text())
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
