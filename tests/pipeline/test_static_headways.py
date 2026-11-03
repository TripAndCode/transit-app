"""Each archived timetable keeps its per-route scheduled headway medians."""


def test_the_medians_table_is_keyed_by_content_route_and_version(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = 'route_headway_by_content'::regclass AND i.indisprimary "
            "ORDER BY a.attname"
        )
        assert [row[0] for row in cur.fetchall()] == ["agency_id", "content_hash", "logic_version", "route_code"]
