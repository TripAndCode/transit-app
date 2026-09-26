"""Migration version numbers must be unique.

`db.migrate._run_up` resolves a version by globbing ``NNNN_*.up.sql`` and
running ``matches[0]``. Two files sharing a number therefore apply only the
alphabetically-first one and record the number as done, so the other never
runs -- on every database, silently, forever. A branch written while a
number was free can collide with one merged in the meantime, which is
exactly when nobody is looking at the numbering.
"""

import collections
import pathlib

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"


def _versions(suffix: str) -> list[str]:
    return [p.name.split("_", 1)[0] for p in MIGRATIONS.glob(f"*.{suffix}.sql")]


def test_no_two_migrations_share_a_version_number():
    dupes = {v: c for v, c in collections.Counter(_versions("up")).items() if c > 1}
    assert not dupes, (
        f"Duplicate migration versions {sorted(dupes)}: only the "
        "alphabetically-first file for a version is ever applied. Renumber "
        "the newer one to the next free version."
    )


def test_every_up_migration_has_a_down():
    ups = {p.name[: -len(".up.sql")] for p in MIGRATIONS.glob("*.up.sql")}
    downs = {p.name[: -len(".down.sql")] for p in MIGRATIONS.glob("*.down.sql")}
    assert ups == downs, f"Unpaired migrations: {sorted(ups ^ downs)}"
