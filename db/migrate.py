"""Plain-SQL migration runner backed by a schema_migrations tracking table.

Migrations live in db/migrations/ as NNNN_name.up.sql / .down.sql pairs and
are applied in filename order inside a transaction (rollback on failure).
Driven by `gtfs_pipeline.py migrate up|down`.
"""

import functools
import logging
import pathlib
import re

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"

# A down migration declares itself destructive by giving one of its header
# comment lines this prefix (see db/migrations/README.md). `migrate_down`
# refuses to run such a migration unless force_destructive=True.
_DESTRUCTIVE_MARKER = "-- DESTRUCTIVE"


class DestructiveMigrationError(RuntimeError):
    """Raised when a `-- DESTRUCTIVE`-marked down migration runs without
    force_destructive=True."""


def is_destructive_down(sql: str) -> bool:
    """True if a down migration's text declares itself destructive.

    Detects a comment line starting with the exact `-- DESTRUCTIVE` marker
    (after stripping surrounding whitespace), not just the phrase appearing
    somewhere mid-line, so a line that merely mentions the marker in passing
    does not trip the gate.
    """
    return any(line.strip().startswith(_DESTRUCTIVE_MARKER) for line in sql.splitlines())


@functools.cache
def rebuildable_agg_tables() -> frozenset[str]:
    """Aggregates a later analyze run rebuilds in full from source data: its
    rebuild set and its agg_meta bookkeeping, plus agg_route_dow and
    agg_stop_seq, which analyze built until they were retired. Any other
    `agg_*` table, such as one analyze only upserts or one an ingest fills,
    holds rows no rebuild restores.

    Imported lazily: the API's health poll imports this module, and must not
    pull in analyze with it.
    """
    from pipeline.analyze import _AGG_TABLES_ORDERED

    return frozenset(_AGG_TABLES_ORDERED) | {"agg_meta", "agg_route_dow", "agg_stop_seq"}


_SPECIAL = re.compile(r"""--|/\*|['";$]""")
_DOLLAR_QUOTE = re.compile(r"\$(?:[^\W\d]\w*)?\$")


def _is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch in "_$")


def _quoted_end(sql: str, start: int) -> int:
    """The index just past the quoted text opening at *start*. A doubled quote
    escapes itself; in an E'' string a backslash escapes the next character."""
    quote = sql[start]
    backslash = quote == "'" and sql[start - 1 : start] in ("e", "E") and not _is_word_char(sql[start - 2 : start - 1])
    pos = start + 1
    while pos < len(sql):
        if backslash and sql[pos] == "\\":
            pos += 2
        elif sql[pos] != quote:
            pos += 1
        elif sql.startswith(quote * 2, pos):
            pos += 2
        else:
            return pos + 1
    return len(sql)


def _top_level_statements(sql: str) -> list[str]:
    """The statements of *sql*, split at the semicolons outside quotes and
    comments.

    A comment becomes a space, a string literal `''` and a dollar-quoted body
    `$$`, so no text, quote or semicolon inside them reads as SQL. A quoted
    identifier is kept, since it may name a table. Block comments nest, as
    in Postgres. A `$` after an identifier character, or before a digit, does
    not open a dollar quote.
    """
    statements: list[str] = []
    current: list[str] = []
    pos = 0
    while (special := _SPECIAL.search(sql, pos)) is not None:
        start = special.start()
        current.append(sql[pos:start])
        token = special.group()
        if token == "--":
            end = sql.find("\n", start)
            pos = len(sql) if end < 0 else end
            current.append(" ")
        elif token == "/*":
            depth, pos = 1, start + 2
            while depth and pos < len(sql):
                step = sql[pos : pos + 2]
                depth += 1 if step == "/*" else -1 if step == "*/" else 0
                pos += 2 if step in ("/*", "*/") else 1
            current.append(" ")
        elif token == "'":
            pos = _quoted_end(sql, start)
            current.append("''")
        elif token == '"':
            pos = _quoted_end(sql, start)
            current.append(sql[start:pos])
        elif token == ";":
            statements.append("".join(current))
            current = []
            pos = start + 1
        elif (dollar := _DOLLAR_QUOTE.match(sql, start)) and not _is_word_char(sql[start - 1 : start]):
            end = sql.find(dollar.group(), dollar.end())
            pos = len(sql) if end < 0 else end + len(dollar.group())
            current.append("$$")
        else:
            current.append(token)
            pos = start + 1
    current.append(sql[pos:])
    statements.append("".join(current))
    return [statement for statement in statements if statement.strip()]


_NAME = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
_TABLE = rf"{_NAME}(?:\s*\.\s*{_NAME})?"
_TABLES = rf"{_TABLE}(?:\s*,\s*{_TABLE})*"
# In ALTER TABLE, a DROP followed by none of these drops a column, with or
# without the COLUMN keyword.
_KEPT_ON_ALTER_DROP = r"(?:CONSTRAINT|DEFAULT|NOT|EXPRESSION|IDENTITY)\b"
_DATA_LOSS = re.compile(
    rf"^\s*(?:DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<drop>{_TABLES})"
    rf"|DELETE\s+FROM\s+(?:ONLY\s+)?(?P<delete>{_TABLE})"
    rf"|TRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?(?P<truncate>{_TABLES})"
    rf"|ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?P<alter>{_TABLE})\s.*?\bDROP\s+(?!{_KEPT_ON_ALTER_DROP}))",
    re.IGNORECASE | re.DOTALL,
)


def _table_names(group: str) -> list[str]:
    """Bare table names in a matched (possibly comma-listed, schema-qualified,
    quoted) group."""
    names = re.findall(_TABLE, group)
    return [re.findall(_NAME, name)[-1].strip('"') for name in names]


def data_losing_tables(sql: str) -> list[str]:
    """Tables outside `rebuildable_agg_tables()` that a top-level statement in
    *sql* drops, drops a column from, or deletes or truncates rows of.

    A down migration it flags must carry the `-- DESTRUCTIVE` marker
    (tests/unit/test_migration_destructive_markers.py), and a pending up
    migration it flags stops scripts/dev_upgrade.py until the owner names it.
    The rebuildable aggregates are exempt in both directions: their rows derive
    from source data, and an analyze rebuild restores them. A plain
    analyze-all rebuilds an incremental aggregate only for the dates its
    ledger or its table fingerprint marks, so a migration that deletes such
    rows also bumps that table's registry version, or the rebuild needs FULL=1.

    Scope: DROP TABLE, DELETE FROM, TRUNCATE, and ALTER TABLE ... DROP [COLUMN]
    statements, read past comments, string literals and dollar-quoted bodies.
    An UPDATE is not counted: a backfill in a reviewed migration rewrites
    values on purpose. Not detected: statements inside a DO block or a function
    body, a data-modifying WITH ... DELETE, MERGE ... DELETE, a narrowing ALTER
    COLUMN ... TYPE, DROP SCHEMA/EXTENSION/TYPE ... CASCADE, rows a TRUNCATE
    ... CASCADE reaches in tables it does not name, and a called function that
    deletes. A migration losing data that way needs the marker, or the owner's
    word, by its author's own judgment.
    """
    rebuildable = rebuildable_agg_tables()
    tables: list[str] = []
    for statement in _top_level_statements(sql):
        m = _DATA_LOSS.match(statement)
        if not m:
            continue
        group = m.group("drop") or m.group("delete") or m.group("truncate") or m.group("alter")
        tables.extend(name for name in _table_names(group) if name.lower() not in rebuildable)
    return tables


_CREATE_TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@functools.lru_cache(maxsize=4)
def _versions_in(directory: pathlib.Path) -> tuple[str, ...]:
    """Migration versions in *directory*, in order.

    Keyed on the directory rather than cached globally. A single shared entry
    would outlive a change to `_MIGRATIONS_DIR` -- which is how a test that
    points it at a `tmp_path` leaves a later real run reading the temporary
    directory's versions. That does not fail loudly: `migrate_up` computes an
    empty pending set, returns without committing, and the connection sits
    idle in a transaction until the next fixture's DDL blocks behind it and
    the job burns its timeout. Keying on the path makes that impossible
    rather than asking every test to remember a reset.

    A tuple, not a list, because an lru_cache handing out a mutable value
    lets one caller's edit reach every later one.
    """
    return tuple(f.name.split("_")[0] for f in sorted(directory.glob("*.up.sql")))


def _versions_on_disk() -> list[str]:
    """On-disk migration versions, in order.

    Cached via :func:`_versions_in`: the directory's contents are fixed for
    the life of the process (nothing in it changes without a deploy), but this
    is read on every board/ops poll (pipeline.health.migration_status) as well
    as every `migrate up`/`pending_migrations` call, so an uncached glob turns
    an O(1) health check into an O(migration count) directory scan on a hot
    path.
    """
    return list(_versions_in(_MIGRATIONS_DIR))


def reset_for_tests() -> None:
    """Drop the cached migration lists.

    Pointing `_MIGRATIONS_DIR` at another directory no longer needs this --
    that is keyed. This is for the remaining case: files appearing in a
    directory already read once, which only a test does.
    """
    _versions_in.cache_clear()


def _applied_versions(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {r[0] for r in cur.fetchall()}


def pending_migrations(conn) -> list[str]:
    """On-disk migration versions not yet applied, in order. Read-only.

    If the schema_migrations table is absent (never-migrated DB), every on-disk
    version is pending. Does not apply or write anything (unlike migrate_up).
    """
    on_disk = _versions_on_disk()
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('schema_migrations')")
        if cur.fetchone()[0] is None:
            return list(on_disk)
    applied = _applied_versions(conn)
    return [v for v in on_disk if v not in applied]


def _run_up(version: str, conn) -> None:
    """Apply one up-migration and record its version, atomically."""
    matches = sorted(_MIGRATIONS_DIR.glob(f"{version}_*.up.sql"))
    if not matches:
        raise FileNotFoundError(f"No up migration file for version {version}")
    sql = matches[0].read_text()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"  Applied: {matches[0].name}")


def _down_migration_path(version: str) -> pathlib.Path:
    """The down-migration file for *version*.

    Shared by the pre-flight scan and the runner so both resolve a version the
    same way -- a scan that looked at a different file than the one that runs
    would clear a rollback it never inspected.
    """
    matches = sorted(_MIGRATIONS_DIR.glob(f"{version}_*.down.sql"))
    if not matches:
        raise FileNotFoundError(f"No down migration file for version {version}")
    return matches[0]


def _destructive_versions(versions: list[str]) -> list[str]:
    """Which of *versions* have a down migration marked `-- DESTRUCTIVE`."""
    return [v for v in versions if is_destructive_down(_down_migration_path(v).read_text())]


def _run_down(version: str, conn, *, force_destructive: bool) -> None:
    """Run one down-migration and delete its version row, atomically."""
    path = _down_migration_path(version)
    sql = path.read_text()
    # Also checked ahead of the loop in `migrate_down`; kept here so the
    # invariant survives a direct call to this function.
    if is_destructive_down(sql) and not force_destructive:
        raise DestructiveMigrationError(
            f"{path.name} is marked `-- DESTRUCTIVE` and will not run without "
            "force_destructive=True (CLI: `migrate down --force-destructive`)."
        )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("DELETE FROM schema_migrations WHERE version=%s", (version,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"  Rolled back: {path.name}")


def migrate_up(conn) -> None:
    """Apply every migration on disk that is not yet in schema_migrations."""
    with conn.cursor() as cur:
        cur.execute(_CREATE_TRACKING)
    conn.commit()
    all_v = _versions_on_disk()
    applied = _applied_versions(conn)
    pending = [v for v in all_v if v not in applied]
    if not pending:
        logger.info("Already up to date.")
        return
    for v in pending:
        _run_up(v, conn)
    logger.info(f"Applied {len(pending)} migration(s).")


def migrate_down(target: str | None, conn, *, force_destructive: bool = False) -> None:
    """Roll back the most recently applied migration (one step).

    Refuses (raises `DestructiveMigrationError`) if any migration it would
    roll back is marked `-- DESTRUCTIVE`, unless force_destructive=True.

    The whole range is scanned before anything runs, and the refusal names
    every marked version it found. Each down migration commits on its own, so
    checking them one at a time inside the loop would roll back everything
    ahead of the first marked one and only then raise -- leaving the schema
    somewhere the operator never asked for, reachable again only by going
    forward. Refusing the request entire is the recoverable direction: the
    operator can re-issue with a nearer `--target`.
    """
    with conn.cursor() as cur:
        cur.execute(_CREATE_TRACKING)
    conn.commit()
    applied = sorted(_applied_versions(conn), reverse=True)
    if not applied:
        logger.info("Nothing to roll back.")
        return
    if target is None:
        to_roll = [applied[0]]
    else:
        to_roll = [v for v in applied if v > target]
        to_roll.sort(reverse=True)
    if not to_roll:
        logger.info(f"Already at or before version {target}.")
        return
    if not force_destructive:
        marked = _destructive_versions(to_roll)
        if marked:
            raise DestructiveMigrationError(
                "refusing to roll back: "
                + ", ".join(_down_migration_path(v).name for v in marked)
                + " is marked `-- DESTRUCTIVE`. Nothing has been rolled back. Re-run with "
                "force_destructive=True (CLI: `migrate down --force-destructive`), or pass a "
                "`--target` that stops short of it."
            )
    for v in to_roll:
        _run_down(v, conn, force_destructive=force_destructive)
    logger.info(f"Rolled back {len(to_roll)} migration(s).")
