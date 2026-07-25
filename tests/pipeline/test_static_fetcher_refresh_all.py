"""refresh_all must skip soft-deleted agencies."""

from unittest.mock import patch

from pipeline.static_fetcher import refresh_all


def test_refresh_all_skips_deleted_agency(pg_conn, tmp_path):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, static_url, static_strategy) "
            "VALUES (%s, %s, %s, %s) RETURNING agency_id",
            ("Static Active", "http://static-active.example.com", "http://static.example.com/a.zip", "direct_url"),
        )
        active_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, static_url, static_strategy, deleted_at) "
            "VALUES (%s, %s, %s, %s, now()) RETURNING agency_id",
            ("Static Deleted", "http://static-deleted.example.com", "http://static.example.com/d.zip", "direct_url"),
        )
        deleted_id = cur.fetchone()[0]
    pg_conn.commit()

    with patch("pipeline.static_fetcher.refresh_static", return_value=None) as fake:
        refresh_all(pg_conn, tmp_path)

    fetched_ids = [c.args[0] for c in fake.call_args_list]
    assert active_id in fetched_ids
    assert deleted_id not in fetched_ids


def test_refresh_all_continues_past_one_agencys_failure(pg_conn, tmp_path):
    """One agency's refresh_static raising must not abort the rest of the
    run - matching cmd_analyze_all's per-agency isolation. Uses a real
    connection (not a mock) so the fix's rollback is genuinely exercised:
    without it, psycopg2 leaves the transaction aborted and the next
    agency's own query fails too."""
    with pg_conn.cursor() as cur:
        ids = []
        for i in range(3):
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url, static_url, static_strategy) "
                "VALUES (%s, %s, %s, %s) RETURNING agency_id",
                (f"Static {i}", f"http://static-{i}.example.com", f"http://static.example.com/{i}.zip", "direct_url"),
            )
            ids.append(cur.fetchone()[0])
    pg_conn.commit()

    def fake_refresh_static(aid, conn, dest_dir):
        if aid == ids[1]:
            raise ValueError("boom")
        return None

    with patch("pipeline.static_fetcher.refresh_static", side_effect=fake_refresh_static) as fake:
        n = refresh_all(pg_conn, tmp_path)

    assert [c.args[0] for c in fake.call_args_list] == ids
    assert n == 0  # both successful calls returned None (no change)
    # Connection must still be usable after the fix's rollback - the fixture's
    # own TRUNCATE teardown will raise if it isn't.
    with pg_conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)
