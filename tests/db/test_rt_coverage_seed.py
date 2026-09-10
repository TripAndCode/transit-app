"""Migration 0043's carry-over seed for the previously-hardcoded feeds.

The seed exists to preserve the three Hiroshima verdicts a hardcoded Python
set used to assert, and nothing else. It identifies them by `feed_url`
rather than by `agency_id` because `agency_id` is a SERIAL: on a deployment
whose agencies were created through the admin API, ids 8/9/10 are merely the
8th-10th rows inserted and could be any operator, and handing an unprobed
feed four permanent affirmative verdicts is exactly the wire-shape-implies-
coverage inference the registry replaced.

Drives the statement straight out of the migration file so the guard these
tests pin is the one a `migrate up` actually runs.
"""

import csv
import pathlib

import pytest

from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIGRATION = ROOT / "db" / "migrations" / "0043_rt_field_coverage_probes.up.sql"
_SEED_MARKER = "INSERT INTO rt_field_coverage_probes"

# The agencies whose coverage the removed constant asserted. Read out of the
# pinned seed CSV rather than restated here, so a typo in either the CSV or
# the migration's URL list shows up as a failure instead of as a silently
# unseeded feed.
_CARRIED_OVER_IDS = (8, 9, 10)


def _seed_sql() -> str:
    text = _MIGRATION.read_text(encoding="utf-8")
    return text[text.index(_SEED_MARKER) :]


@pytest.fixture(scope="module")
def carried_over_feeds() -> list[str]:
    with (ROOT / "agencies.csv").open(encoding="utf-8") as f:
        rows = {int(r["agency_id"]): r["feed_url"] for r in csv.DictReader(f) if r["agency_id"].strip()}
    feeds = [rows[aid] for aid in _CARRIED_OVER_IDS]
    assert len(set(feeds)) == len(_CARRIED_OVER_IDS)
    return feeds


def _insert_agency(pg_conn, agency_id, name, feed_url, strategy="static_join"):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_id, agency_name, feed_url, ingest_strategy) VALUES (%s, %s, %s, %s)",
            (agency_id, name, feed_url, strategy),
        )
    pg_conn.commit()


def _probe_rows(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id, field_name, confirmed, source_feed, expires_at "
            "FROM rt_field_coverage_probes ORDER BY agency_id, field_name"
        )
        return cur.fetchall()


def test_seed_carries_over_the_three_pinned_feeds(pg_conn, carried_over_feeds):
    """Identified by feed, so the carry-over lands wherever those feeds live
    -- including a deployment that assigned them different agency_ids."""
    ids = [301, 302, 303]
    for aid, feed in zip(ids, carried_over_feeds, strict=True):
        _insert_agency(pg_conn, aid, f"Carried {aid}", feed)

    with pg_conn.cursor() as cur:
        cur.execute(_seed_sql())
    pg_conn.commit()

    rows = _probe_rows(pg_conn)
    assert len(rows) == len(ids) * len(RT_COVERAGE_FIELDS)
    assert {r[0] for r in rows} == set(ids)
    by_agency = dict(zip(ids, carried_over_feeds, strict=True))
    for agency_id, field_name, confirmed, source_feed, expires_at in rows:
        assert field_name in RT_COVERAGE_FIELDS
        assert confirmed is True
        # The verdict is backed by checked-in captures of these exact feeds,
        # so it is recorded against the feed and does not decay.
        assert source_feed == by_agency[agency_id]
        assert expires_at is None


def test_seed_is_a_no_op_for_another_operator_holding_the_same_ids(pg_conn):
    """Ids 8/9/10 on a differently-seeded database are some other operator's,
    and must not inherit a verdict nobody probed their feeds for."""
    for aid in _CARRIED_OVER_IDS:
        _insert_agency(pg_conn, aid, f"Unrelated {aid}", f"https://unrelated.example/{aid}/tu.bin")

    with pg_conn.cursor() as cur:
        cur.execute(_seed_sql())
    pg_conn.commit()

    assert _probe_rows(pg_conn) == []


def test_seed_skips_a_carried_over_feed_on_another_ingest_strategy(pg_conn, carried_over_feeds):
    """A feed no longer ingested by the strategy that can populate these
    fields gets nothing: the registry only ever holds rows the strategy half
    of the gate could act on."""
    _insert_agency(pg_conn, 304, "Restrategised", carried_over_feeds[0], strategy="aomori_regex")

    with pg_conn.cursor() as cur:
        cur.execute(_seed_sql())
    pg_conn.commit()

    assert _probe_rows(pg_conn) == []


def test_seed_is_a_no_op_on_a_database_with_none_of_those_feeds(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_seed_sql())
    pg_conn.commit()

    assert _probe_rows(pg_conn) == []
