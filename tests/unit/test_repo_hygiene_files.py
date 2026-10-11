"""Repo-hygiene files: SECURITY.md, the PR template, the Dependabot config,
.gitattributes, and the local-only trees .gitignore has to keep out.

Filesystem and `git` only, no DB, so this belongs under `tests/unit/` per
AGENTS.md's convention.
"""

from __future__ import annotations

import json
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_security_md_exists_and_mentions_reporting():
    path = REPO_ROOT / "SECURITY.md"
    assert path.is_file()
    content = path.read_text()
    assert "security advisory" in content.lower()


def test_pull_request_template_has_origin_field():
    path = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
    assert path.is_file()
    content = path.read_text()
    assert "**Origin:**" in content


def test_dependabot_config_is_valid_and_covers_npm_pip_and_actions():
    """Parsed, not grepped.

    An unparseable or mis-shaped dependabot.yml is not an error GitHub
    reports anywhere this repo would notice — it simply stops opening update
    PRs. A substring check on the raw text passes in exactly that case, so
    the guard has to go through the YAML.
    """
    path = REPO_ROOT / ".github" / "dependabot.yml"
    assert path.is_file()
    config = yaml.safe_load(path.read_text())

    assert config["version"] == 2
    updates = config["updates"]
    # "docker" appears once per directory that holds a Dockerfile, so
    # ecosystems are grouped by name here rather than assumed unique per entry.
    ecosystems = {entry["package-ecosystem"] for entry in updates}
    assert ecosystems == {"npm", "pip", "github-actions", "docker", "docker-compose"}

    for entry in updates:
        ecosystem = entry["package-ecosystem"]
        assert all(d.startswith("/") for d in _directories(entry)), ecosystem
        assert entry["schedule"]["interval"] == "weekly", ecosystem
        # Every group must actually select something; an empty group is
        # accepted by the parser and silently does nothing.
        for name, group in entry.get("groups", {}).items():
            assert group.get("update-types"), f"{ecosystem}: group {name} selects no updates"

    npm_entries = [entry for entry in updates if entry["package-ecosystem"] == "npm"]
    assert len(npm_entries) == 1
    assert npm_entries[0]["directory"] == "/frontend", "npm manifests live in frontend/"

    docker_dirs = {d for entry in updates if entry["package-ecosystem"] == "docker" for d in _directories(entry)}
    assert docker_dirs == {"/", "/db"}, "docker ecosystem should cover every directory with a Dockerfile"


def _directories(entry: dict) -> list[str]:
    return entry["directories"] if "directories" in entry else [entry["directory"]]


def test_dependabot_docker_compose_covers_every_compose_file():
    """The `docker` ecosystem never reads a compose file, so a compose file's
    image pins are updated only if its directory is listed under
    `docker-compose`."""
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    covered = {
        d for entry in config["updates"] if entry["package-ecosystem"] == "docker-compose" for d in _directories(entry)
    }
    tracked = subprocess.run(
        ["git", "ls-files", "--", ":(glob)**/compose.yml"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    parents = {Path(path).parent.as_posix() for path in tracked}
    directories = {"/" if parent == "." else f"/{parent}" for parent in parents}
    assert tracked
    assert directories <= covered, f"compose files outside dependabot's docker-compose scan: {directories - covered}"


def test_clickhouse_server_pins_agree():
    """Dependabot bumps only the compose files; the workflow service images and
    the script literals are edited by hand, so a bump that misses one runs CI
    against a different server than the one deployed."""
    pin = re.compile(r"clickhouse/clickhouse-server:([\w.\-]+)")
    files = [
        "compose.yml",
        "deploy/vps/compose.yml",
        "Makefile",
        "scripts/run_full_ci.sh",
        *(f".github/workflows/{p.name}" for p in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml")),
    ]
    found = {f: set(pin.findall((REPO_ROOT / f).read_text())) for f in files if pin.search((REPO_ROOT / f).read_text())}
    assert {"compose.yml", "deploy/vps/compose.yml", "Makefile", "scripts/run_full_ci.sh"} <= set(found)
    tags = {tag for pins in found.values() for tag in pins}
    assert len(tags) == 1, f"clickhouse-server pins disagree: {found}"


def test_dependabot_github_actions_scans_the_composite_actions():
    """`directory: /` reads only the workflows. Each composite action pins its
    own `uses:` versions, so its directory has to be matched by a listed glob
    or those pins drift from the workflows' unnoticed."""
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    entry = next(e for e in config["updates"] if e["package-ecosystem"] == "github-actions")
    globs = entry["directories"]
    actions = sorted((REPO_ROOT / ".github" / "actions").glob("*/action.y*ml"))
    assert actions, "no composite actions found; the scan no longer guards anything"
    for action in actions:
        directory = "/" + action.parent.relative_to(REPO_ROOT).as_posix()
        assert any(fnmatch(directory, pattern) for pattern in globs), f"{directory} is not scanned"


def test_dependabot_cannot_swamp_the_single_ci_runner():
    """Every update PR costs a full run on the one runner that also gates
    merges, and none of these three settings fails loudly when dropped —
    the symptom is a queue nobody can get through, a week later.

    The defaults are the failure: five open PRs per ecosystem is fifteen
    runs, rebased again on every push to `main`, arriving on whatever
    weekday the config happened to land.
    """
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())

    for entry in config["updates"]:
        ecosystem = entry["package-ecosystem"]
        assert entry.get("rebase-strategy") == "disabled", (
            f"{ecosystem}: rebasing every open PR on each push to main floods the runner"
        )
        limit = entry.get("open-pull-requests-limit")
        assert limit is not None and limit <= 3, f"{ecosystem}: unbounded batch (limit={limit})"
        schedule = entry["schedule"]
        assert schedule.get("day") in {"saturday", "sunday"}, (
            f"{ecosystem}: a weekday batch blocks the merge queue during working hours"
        )


def test_dependabot_keeps_a_vitest_major_in_one_pr():
    """vitest and its `@vitest/*` plugins pin each other's exact version, so a
    major bumped on one side alone fails every test file at coverage
    collection. A pattern typo would still parse and simply stop grouping."""

    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    npm = next(entry for entry in config["updates"] if entry["package-ecosystem"] == "npm")
    group = npm["groups"]["vitest-major"]
    assert group["update-types"] == ["major"]

    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    vitest_family = [name for name in dependencies if name == "vitest" or name.startswith("@vitest/")]
    assert "vitest" in vitest_family and len(vitest_family) > 1

    def grouped(name: str) -> bool:
        return any(fnmatch(name, pattern) for pattern in group["patterns"])

    assert all(grouped(name) for name in vitest_family), vitest_family
    for unrelated in ("vite", "@vitejs/plugin-react", "eslint"):
        assert not grouped(unrelated), unrelated


def test_dependabot_leaves_a_postgres_major_to_a_planned_migration():
    """db/'s extensions follow the base image's own major, so a major bump
    builds and passes CI while the server it starts cannot read an existing
    data volume. A typo in the ignore rule would still parse and let that PR
    through; an over-broad rule would also drop the minor and patch fixes."""

    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    db = next(
        entry for entry in config["updates"] if entry["package-ecosystem"] == "docker" and entry["directory"] == "/db"
    )
    base = re.search(r"^FROM\s+([^\s:@]+)", (REPO_ROOT / "db" / "Dockerfile").read_text(), re.MULTILINE)
    assert base, "db/Dockerfile has no FROM line"

    rules = [rule for rule in db.get("ignore", []) if fnmatch(base.group(1), rule["dependency-name"])]
    assert rules, f"db/: nothing stops Dependabot proposing a {base.group(1)} major"
    assert all(rule.get("update-types") == ["version-update:semver-major"] for rule in rules), rules


def test_gitattributes_normalizes_line_endings_and_marks_binaries():
    path = REPO_ROOT / ".gitattributes"
    assert path.is_file()
    content = path.read_text()
    assert "* text=auto eol=lf" in content
    for pattern in ("*.zip binary", "*.pb binary", "*.bin binary", "*.png binary", "*.jpg binary"):
        assert pattern in content


def test_every_tracked_binary_extension_is_declared_binary():
    """`* text=auto eol=lf` asks git to guess for anything not marked binary.

    The guess is good but not free, so every binary extension actually in the
    tree should be declared rather than inferred. This fails when a new binary
    type is committed without a matching .gitattributes line.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    declared = {
        line.split()[0].removeprefix("*")
        for line in (REPO_ROOT / ".gitattributes").read_text().splitlines()
        if line.strip().endswith(" binary")
    }
    binary_like = {".zip", ".pb", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2"}
    present = {suffix for suffix in (Path(p).suffix for p in tracked) if suffix in binary_like}
    assert present <= declared, f"tracked binary types missing from .gitattributes: {sorted(present - declared)}"


def _is_ignored(relative_path: str) -> bool:
    """Ask git, rather than reading .gitignore and reimplementing its matching."""
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", relative_path],
            cwd=REPO_ROOT,
        ).returncode
        == 0
    )


def test_local_only_trees_stay_ignored():
    """docs/ is tracked by default, so this enumeration is the only thing
    keeping local planning material and Finder droppings out of a `git add
    -A`. Asking git directly means the test fails when a name is dropped from
    .gitignore, not merely when the file's text changes.
    """
    for path in (
        "docs/superpowers/notes.md",
        "docs/specs/plan.md",
        "docs/references/reference.md",
        "docs/.DS_Store",
        ".DS_Store",
        "frontend/src/.DS_Store",
        ".env",
        ".env.local",
    ):
        assert _is_ignored(path), f"{path} is no longer ignored"


def test_tracked_docs_and_env_example_are_not_ignored():
    """The negative control: a rule broad enough to catch everything above
    must still leave the real docs and .env.example committable."""
    for path in (
        "docs/deploy-railway.md",
        "docs/features/ask-tab.md",
        "docs/ops-monitoring-deploy.md",
        ".env.example",
    ):
        assert not _is_ignored(path), f"{path} is ignored but must stay tracked"
