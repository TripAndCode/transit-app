"""hms_to_sec_sql is the one Postgres-side parse of GTFS static times."""

import pytest

from pipeline.db import hms_to_sec_sql


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("08:30:15", 8 * 3600 + 30 * 60 + 15),
        ("7:05", 7 * 3600 + 5 * 60),
        ("25:30:00", 25 * 3600 + 30 * 60),
        ("7:75:00", None),
        ("07:30:75", None),
        ("07:60:00", None),
        ("noon", None),
        ("", None),
    ],
)
def test_hms_to_sec_sql(pg_conn, text, expected):
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT {hms_to_sec_sql('t')} FROM (SELECT %s::text AS t) x", (text,))
        assert cur.fetchone()[0] == expected
