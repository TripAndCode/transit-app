"""Content-level pairing check for migration 0060, beyond the generic
filename-pairing covered by ``test_migration_numbering.py``.

Parses the two SQL files as text (no DB available) and checks that every
object the up-migration creates or drops is reversed by the down-migration:
the same index names, and the same foreign-key constraint name restored to
its pre-migration definition.
"""

import pathlib
import re

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
UP = (MIGRATIONS / "0060_admin_indexes.up.sql").read_text()
DOWN = (MIGRATIONS / "0060_admin_indexes.down.sql").read_text()

_CREATE_INDEX_RE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE)
_DROP_INDEX_RE = re.compile(r"DROP\s+INDEX\s+IF\s+EXISTS\s+(\w+)", re.IGNORECASE)


def test_up_creates_the_four_new_indexes():
    created = set(_CREATE_INDEX_RE.findall(UP))
    assert created == {
        "idx_ask_query_log_created_id",
        "idx_admin_audit_actor_at",
        "idx_admin_audit_action_at",
        "pipeline_runs_running_started_at_idx",
    }


def test_up_drops_the_superseded_login_events_index():
    assert _DROP_INDEX_RE.findall(UP) == ["idx_login_events_user_id"]


def test_down_drops_every_index_the_up_created():
    created = set(_CREATE_INDEX_RE.findall(UP))
    dropped_by_down = set(_DROP_INDEX_RE.findall(DOWN))
    assert created <= dropped_by_down, f"down.sql is missing DROP INDEX for {created - dropped_by_down}"


def test_down_recreates_the_index_the_up_dropped():
    """0009's idx_login_events_user_id, dropped by up, must come back in down."""
    recreated = set(_CREATE_INDEX_RE.findall(DOWN))
    assert "idx_login_events_user_id" in recreated


def test_down_does_not_drop_indexes_it_did_not_create():
    """down.sql should only ever drop what this migration's up.sql created,
    never reach past it into an earlier migration's index."""
    dropped_by_down = set(_DROP_INDEX_RE.findall(DOWN))
    created_by_up = set(_CREATE_INDEX_RE.findall(UP))
    assert dropped_by_down <= created_by_up


_FK_RE = re.compile(
    r"ALTER TABLE\s+pipeline_runs\s+ADD CONSTRAINT\s+(\w+)\s+FOREIGN KEY\s*\(agency_id\)\s*"
    r"REFERENCES agencies\(agency_id\)(\s+ON DELETE (SET NULL|CASCADE|RESTRICT|NO ACTION))?",
    re.IGNORECASE,
)


def test_up_adds_agency_fk_with_on_delete_set_null():
    matches = _FK_RE.findall(UP)
    assert len(matches) == 1, (
        f"expected exactly one ADD CONSTRAINT for pipeline_runs.agency_id in up.sql, got {matches}"
    )
    name, _, on_delete = matches[0]
    assert on_delete.upper() == "SET NULL"
    assert f"DROP CONSTRAINT IF EXISTS {name}" in UP


def test_down_restores_the_agency_fk_without_on_delete_set_null():
    up_name = _FK_RE.findall(UP)[0][0]
    matches = _FK_RE.findall(DOWN)
    assert len(matches) == 1, (
        f"expected exactly one ADD CONSTRAINT for pipeline_runs.agency_id in down.sql, got {matches}"
    )
    down_name, _, on_delete = matches[0]
    assert down_name == up_name
    assert on_delete == ""
    assert f"DROP CONSTRAINT IF EXISTS {down_name}" in DOWN


def _leading_comment_block(sql: str) -> str:
    lines: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped == "" or stripped.startswith("--"):
            lines.append(line)
            continue
        break
    return "\n".join(lines)


def test_header_comment_names_every_index_its_serving_query():
    """The leading comment block must name every index this migration
    creates or drops, describing what query it serves -- not just a bare
    CREATE INDEX with no rationale."""
    header = _leading_comment_block(UP)
    for name in (
        "idx_ask_query_log_created_id",
        "idx_admin_audit_actor_at",
        "idx_admin_audit_action_at",
        "pipeline_runs_running_started_at_idx",
        "idx_login_events_user_id",
    ):
        assert name in header, f"{name} is not mentioned in up.sql's header comment"
