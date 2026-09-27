"""`scripts/prebuild_indexes_concurrently.sql` builds exactly what the migrations build.

The script only helps if each index it pre-builds has the name and definition
the migration would build: `IF NOT EXISTS` matches on the name alone, so a
drifted definition would be skipped by the migration and silently shipped.
Parsed as text; no database.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts" / "prebuild_indexes_concurrently.sql").read_text()
MIGRATIONS = ROOT / "db" / "migrations"

_CREATE_INDEX_RE = re.compile(r"^CREATE\s+(UNIQUE\s+)?INDEX\b", re.IGNORECASE)
_CONCURRENTLY_RE = re.compile(r"^(CREATE\s+(?:UNIQUE\s+)?INDEX)\s+CONCURRENTLY\b", re.IGNORECASE)
_GUARD_RE = re.compile(r"^\\if\s+:pending_(\d{4})\s*$")
_GUARD_VAR_RE = re.compile(r"version\s*=\s*'(\d{4})'\s*\)\s*AS\s+pending_(\d{4})", re.IGNORECASE)


def _normalize(statement: str) -> str:
    return " ".join(statement.split())


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _migration_create_indexes(version: str) -> set[str]:
    [path] = MIGRATIONS.glob(f"{version}_*.up.sql")
    statements = (_normalize(s) for s in _strip_comments(path.read_text()).split(";"))
    return {s for s in statements if _CREATE_INDEX_RE.match(s)}


def _script_create_indexes() -> list[tuple[str | None, str]]:
    """Each CREATE INDEX in the script, with the migration version of the
    `\\if :pending_NNNN` block it sits in (None outside any block)."""
    found: list[tuple[str | None, str]] = []
    guard: str | None = None
    pending = ""
    for line in _strip_comments(SCRIPT).splitlines():
        stripped = line.strip()
        if stripped.startswith("\\"):
            match = _GUARD_RE.match(stripped)
            if match:
                guard = match.group(1)
            elif stripped == "\\endif":
                guard = None
            pending = ""
            continue
        pending += " " + stripped
        while ";" in pending:
            statement, pending = pending.split(";", 1)
            statement = _normalize(statement)
            if _CREATE_INDEX_RE.match(statement):
                found.append((guard, statement))
    return found


def test_the_script_builds_indexes():
    assert _script_create_indexes()


def test_every_index_is_built_concurrently_and_only_if_missing():
    for _, statement in _script_create_indexes():
        assert _CONCURRENTLY_RE.match(statement), statement
        assert re.search(r"\bCONCURRENTLY\s+IF\s+NOT\s+EXISTS\b", statement, re.IGNORECASE), statement


def test_every_index_matches_its_guarding_migration():
    """Each statement, minus CONCURRENTLY, is one its guard's migration runs.

    The guard is what makes the script a no-op once that migration is applied,
    so it has to name the migration that actually builds the index."""
    for guard, statement in _script_create_indexes():
        assert guard is not None, f"not inside a `\\if :pending_NNNN` block: {statement}"
        as_migrated = _CONCURRENTLY_RE.sub(r"\1", statement)
        assert as_migrated in _migration_create_indexes(guard), (
            f"{statement!r} does not match any CREATE INDEX in migration {guard}"
        )


def test_every_guard_tests_its_own_migration():
    """`pending_NNNN` must be computed from version 'NNNN'."""
    pairs = _GUARD_VAR_RE.findall(SCRIPT)
    assert pairs
    for checked, name in pairs:
        assert checked == name
    guards = {guard for guard, _ in _script_create_indexes()}
    assert guards <= {name for _, name in pairs}


def test_the_script_never_opens_a_transaction():
    """CREATE INDEX CONCURRENTLY refuses to run inside a transaction block."""
    code = _strip_comments(SCRIPT)
    assert not re.search(r"\b(BEGIN|START\s+TRANSACTION)\b", code, re.IGNORECASE)
