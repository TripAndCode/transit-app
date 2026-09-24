"""Plain-SQL migration runner backed by a schema_migrations tracking table.

Migrations live in db/migrations/ as NNNN_name.up.sql / .down.sql pairs and
are applied in filename order inside a transaction (rollback on failure).
Driven by `gtfs_pipeline.py migrate up|down`.
"""

import logging
import pathlib

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


_CREATE_TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _versions_on_disk() -> list[str]:
    files = sorted(_MIGRATIONS_DIR.glob("*.up.sql"))
    return [f.name.split("_")[0] for f in files]


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
