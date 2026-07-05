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
