"""Structural (text-assertion) safety checks for `Makefile` and `.env.example`.

Pure-logic, no DB: these parse the tracked files as text and assert on their
content, so they belong under `tests/unit/` per CLAUDE.md's DB-fixture-bypass
convention. They exist because a Makefile has no type system of its own --
these invariants (a destructive target requires explicit confirmation, quality
gates never silently reformat instead of checking, the test target never
defaults to the real dev database, `.env` still reaches recipes at all, and
the template `.env.example` never ships a value that breaks the Makefile's
own default) can only be caught by reading the text.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _target_recipe(text: str, target: str) -> str:
    """Return the recipe body of *target* (lines after its `target:` header,
    up to the next unindented/blank-separated line), for scoped assertions."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == f"{target}:" or line.startswith(f"{target}:"):
            start = i
            break
    assert start is not None, f"no `{target}:` rule found in Makefile"
    body = []
    for line in lines[start + 1 :]:
        if line.strip() == "" or (line and not line[0].isspace() and not line.startswith("\t")):
            if line.strip() == "":
                continue
            break
        body.append(line)
    return "\n".join(body)


def test_migrate_down_requires_confirm():
    recipe = _target_recipe(MAKEFILE.read_text(), "migrate-down")
    assert "CONFIRM" in recipe


def test_migrate_down_exits_nonzero_without_confirm():
    recipe = _target_recipe(MAKEFILE.read_text(), "migrate-down")
    assert "exit 1" in recipe


def test_check_does_not_depend_on_fmt():
    text = MAKEFILE.read_text()
    check_line = next(line for line in text.splitlines() if line.startswith("check:"))
    prereqs = check_line.split(":", 1)[1].split()
    assert "fmt" not in prereqs
    assert "fmt-check" in prereqs


def test_fmt_check_target_exists_and_only_checks():
    text = MAKEFILE.read_text()
    assert "\nfmt-check:" in text or text.startswith("fmt-check:")
    recipe = _target_recipe(text, "fmt-check")
    assert "--check" in recipe


def test_export_is_file_wide():
    """`.env` reaches recipes only through a bare, file-wide `export`.

    Nothing else loads `.env` — the app and the fetch/sync scripts read bare
    ``os.environ`` / ``${VAR:?}``. An allowlisted ``export VAR1 VAR2`` strips
    every unlisted variable from every recipe's shell, which silently
    unconfigures `serve` and hard-fails `fetch`/`fetch-ingest`/`sync-r2`.
    Dev-database isolation is `test`'s responsibility, asserted separately.
    """
    lines = [line.strip() for line in MAKEFILE.read_text().splitlines()]
    assert "export" in lines, "`.env` no longer reaches recipes; see this test's docstring"


def test_test_target_never_defaults_to_dev_database():
    recipe = _target_recipe(MAKEFILE.read_text(), "test")
    assert ":5433" not in recipe
    assert "run_integration_tests.sh" in recipe


def test_ch_test_tears_down_any_stale_container_first():
    recipe = _target_recipe(MAKEFILE.read_text(), "ch-test")
    assert "docker rm -f transit-test-ch" in recipe


def test_ch_test_down_target_exists():
    text = MAKEFILE.read_text()
    assert "\nch-test-down:" in text


def test_phony_has_single_list_including_ask_eval():
    text = MAKEFILE.read_text()
    phony_lines = [line for line in text.splitlines() if line.startswith(".PHONY:")]
    assert len(phony_lines) == 1, f"expected exactly one .PHONY line, found {len(phony_lines)}"
    targets = phony_lines[0].split(":", 1)[1].split()
    assert targets.count("promote-intent-cache") == 1
    assert "ask-eval" in targets


def test_env_example_has_no_active_database_url():
    lines = ENV_EXAMPLE.read_text().splitlines()
    active = [line for line in lines if line.startswith("DATABASE_URL=")]
    assert active == [], f"active DATABASE_URL line breaks every target after `make bootstrap`: {active}"


def test_env_example_has_no_active_agency_id():
    lines = ENV_EXAMPLE.read_text().splitlines()
    active = [line for line in lines if line.startswith("AGENCY_ID=")]
    assert active == [], f"active AGENCY_ID silently scopes ingest/analyze: {active}"


def test_database_url_has_no_hardcoded_default():
    """An unset DATABASE_URL must stop Make, not resolve to some literal host.

    `.env` is gitignored, so no git worktree has one. A literal default meant
    every database target in a worktree aimed at whatever answered on that
    port -- on a machine running more than one Postgres, another project's.
    """
    text = MAKEFILE.read_text()
    assignment = next(line for line in text.splitlines() if line.startswith("DATABASE_URL ?="))
    assert assignment.strip() == "DATABASE_URL ?=", (
        f"DATABASE_URL must default to empty, not to a literal: {assignment!r}"
    )


def test_database_targets_go_through_the_guarded_expansion():
    """Recipes must use `$(db_url)`, which errors when nothing is configured.

    Naming `$(DATABASE_URL)` directly would pass the empty default straight
    through and fail somewhere deeper, with a message about a connection
    rather than about the missing configuration.
    """
    text = MAKEFILE.read_text()
    assert "db_url = $(if $(DATABASE_URL)," in text, "the guarded expansion is gone"
    direct = [line for line in text.splitlines() if "DATABASE_URL=$(DATABASE_URL)" in line]
    assert direct == [], f"recipes must use $(db_url): {direct}"
