"""The static-joined aggregates' ClickHouse side: the schedule mirror and the stop tables."""

from pipeline import ch_aggregates
from pipeline.analyze import _mirror_static_stop_times

AGENCY = 7201
SEPT = 202609


def _seed_schedule(pg_conn, agency_id: int, rows: list[tuple[str, int, str]]) -> None:
    """(trip_id, stop_sequence, stop_id) rows for the agency's Postgres static_stop_times."""
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM static_stop_times WHERE agency_id = %s", (agency_id,))
        for trip_id, sequence, stop_id in rows:
            cur.execute(
                "INSERT INTO static_stop_times "
                "(agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
                "VALUES (%s, %s, %s, %s, '08:10:00', '08:10:00')",
                (agency_id, trip_id, sequence, stop_id),
            )


def _mirror(ch_client, agency_id: int) -> list[tuple]:
    return sorted(
        ch_client.query(
            "SELECT trip_id, stop_sequence, stop_id FROM static_stop_times WHERE agency_id = {a:Int32}",
            parameters={"a": agency_id},
        ).result_rows
    )


def test_the_mirror_copies_the_agencys_schedule_under_its_fingerprint(pg_conn, agency_id, ch_client):
    _seed_schedule(pg_conn, agency_id, [("t1", 1, "S1"), ("t1", 2, "S2")])
    _mirror_static_stop_times(agency_id, pg_conn, ch_client, has_static=True, fingerprint="fp-1")
    assert _mirror(ch_client, agency_id) == [("t1", 1, "S1"), ("t1", 2, "S2")]
    assert ch_aggregates.static_mirror_fingerprint(ch_client, agency_id) == "fp-1"


def test_a_changed_fingerprint_recopies_the_schedule(pg_conn, agency_id, ch_client):
    _seed_schedule(pg_conn, agency_id, [("t1", 1, "S1")])
    _mirror_static_stop_times(agency_id, pg_conn, ch_client, has_static=True, fingerprint="fp-1")
    _seed_schedule(pg_conn, agency_id, [("t1", 1, "S9")])  # same row count, a changed stop
    _mirror_static_stop_times(agency_id, pg_conn, ch_client, has_static=True, fingerprint="fp-1")
    assert _mirror(ch_client, agency_id) == [("t1", 1, "S1")], "an unchanged fingerprint must not recopy"
    _mirror_static_stop_times(agency_id, pg_conn, ch_client, has_static=True, fingerprint="fp-2")
    assert _mirror(ch_client, agency_id) == [("t1", 1, "S9")]


def test_an_agency_without_a_schedule_has_no_mirror(pg_conn, agency_id, ch_client):
    _seed_schedule(pg_conn, agency_id, [("t1", 1, "S1")])
    _mirror_static_stop_times(agency_id, pg_conn, ch_client, has_static=True, fingerprint="fp-1")
    _mirror_static_stop_times(agency_id, pg_conn, ch_client, has_static=False, fingerprint="fp-0")
    assert _mirror(ch_client, agency_id) == []
    assert ch_aggregates.static_mirror_fingerprint(ch_client, agency_id) is None


def test_mirroring_one_agency_leaves_another_alone(pg_conn, agency_id, ch_client):
    _seed_schedule(pg_conn, agency_id, [("t1", 1, "S1")])
    ch_aggregates.replace_static_stop_times(ch_client, AGENCY, [(AGENCY, "x", 1, "X", None, None)], "other")
    _mirror_static_stop_times(agency_id, pg_conn, ch_client, has_static=True, fingerprint="fp-1")
    assert _mirror(ch_client, AGENCY) == [("x", 1, "X")]
