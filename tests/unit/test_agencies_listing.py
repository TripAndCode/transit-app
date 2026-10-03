"""``pipeline.query.agencies.list_agencies`` — the one agency-listing query.

The public catalogue and the admin catalogue read the same rows and differ
only in whether soft-deleted agencies are included, so they share a query
rather than keeping two copies that can drift.
"""

from datetime import date

from pipeline.query.agencies import agency_row_to_dict, list_agencies


class _StubConn:
    """Records the SQL it was handed and replays canned rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries: list[str] = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        return self.rows


async def test_list_agencies_hides_soft_deleted_rows_by_default():
    conn = _StubConn()
    await list_agencies(conn, include_deleted=False)
    assert "deleted_at IS NULL" in conn.queries[0]


async def test_list_agencies_includes_soft_deleted_rows_when_asked():
    conn = _StubConn()
    await list_agencies(conn, include_deleted=True)
    assert "deleted_at IS NULL" not in conn.queries[0]


async def test_list_agencies_selects_both_callers_columns():
    """The public endpoint needs ``latest_data_date``; the admin one needs the
    ingest fields and ``deleted_at``. One query has to carry all of them —
    each caller's response model drops what it does not name."""
    conn = _StubConn()
    await list_agencies(conn, include_deleted=True)
    sql = conn.queries[0]
    for column in (
        "agency_id",
        "agency_name",
        "feed_url",
        "static_url",
        "ingest_strategy",
        "trip_id_pattern",
        "deleted_at",
        "latest_data_date",
    ):
        assert column in sql


async def test_list_agencies_is_ordered_by_agency_id():
    conn = _StubConn()
    await list_agencies(conn, include_deleted=False)
    assert "ORDER BY a.agency_id" in conn.queries[0]


async def test_list_agencies_normalizes_latest_data_date_to_an_iso_string():
    conn = _StubConn([{"agency_id": 1, "latest_data_date": date(2026, 8, 31)}])
    assert (await list_agencies(conn, include_deleted=False))[0]["latest_data_date"] == "2026-08-31"


def test_agency_row_to_dict_leaves_a_missing_latest_data_date_alone():
    assert agency_row_to_dict({"agency_id": 1, "latest_data_date": None})["latest_data_date"] is None
