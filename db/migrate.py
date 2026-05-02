import pathlib

_MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"

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


def _run_up(version: str, conn) -> None:
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
    print(f"  Applied: {matches[0].name}")


def _run_down(version: str, conn) -> None:
    matches = sorted(_MIGRATIONS_DIR.glob(f"{version}_*.down.sql"))
    if not matches:
        raise FileNotFoundError(f"No down migration file for version {version}")
    sql = matches[0].read_text()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("DELETE FROM schema_migrations WHERE version=%s", (version,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"  Rolled back: {matches[0].name}")


def migrate_up(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE_TRACKING)
    conn.commit()
    all_v = _versions_on_disk()
    applied = _applied_versions(conn)
    pending = [v for v in all_v if v not in applied]
    if not pending:
        print("Already up to date.")
        return
    for v in pending:
        _run_up(v, conn)
    print(f"Applied {len(pending)} migration(s).")


def migrate_down(target: str | None, conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE_TRACKING)
    conn.commit()
    applied = sorted(_applied_versions(conn), reverse=True)
    if not applied:
        print("Nothing to roll back.")
        return
    if target is None:
        to_roll = [applied[0]]
    else:
        to_roll = [v for v in applied if v > target]
        to_roll.sort(reverse=True)
    if not to_roll:
        print(f"Already at or before version {target}.")
        return
    for v in to_roll:
        _run_down(v, conn)
    print(f"Rolled back {len(to_roll)} migration(s).")
