from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from pipeline.ingest import ingest_live, ingest_live_payload
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed


def test_ingest_live_raises_when_no_feed_url():
    mock_conn = MagicMock()
    # feed_url SELECT returns empty string; ValueError is raised before strategy lookup
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [("",)]
    with pytest.raises(ValueError, match="No feed_url"):
        ingest_live(1, mock_conn, MagicMock())


def test_ingest_live_raises_when_agency_not_found():
    mock_conn = MagicMock()
    # First fetchone: agency not found → None triggers ValueError before strategy lookup
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    with pytest.raises(ValueError, match="No feed_url"):
        ingest_live(999, mock_conn, MagicMock())


def test_ingest_live_rejects_unsafe_feed_url():
    """A non-http(s) / internal feed_url is rejected (SSRF guard) - the
    validation itself lives inside safe_urlopen (see
    tests/unit/test_url_guard_safe_urlopen.py); this only proves
    ingest_live surfaces that rejection rather than swallowing it."""
    from pipeline.url_guard import FeedURLError

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [
        ("file:///etc/passwd",),  # feed_url SELECT
        (None,),  # ingest_strategy SELECT -> falls back to aomori_regex
    ]
    with patch("pipeline.ingest.safe_urlopen", side_effect=FeedURLError("blocked")) as mock_safe_urlopen:
        with pytest.raises(FeedURLError):
            ingest_live(1, mock_conn, MagicMock())
    mock_safe_urlopen.assert_called_once()


def test_ingest_live_fetches_and_ingests(tmp_path):
    """Test that ingest_live fetches the URL and calls strategy.parse_feed with raw bytes."""
    mock_conn = MagicMock()
    # Two fetchone calls: (1) feed_url SELECT, (2) ingest_strategy SELECT —
    # resolved once in ingest_live and passed through to ingest_live_payload.
    # Public IP literal so validate_feed_url passes without a DNS lookup (hermetic).
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [
        ("https://8.8.8.8/feed.pb",),
        (None,),  # ingest_strategy = NULL → falls back to aomori_regex
    ]

    fake_bytes = b"fake protobuf data"

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    # Stub out the strategy's parse_feed — returns one 8-tuple row
    fake_strategy_row = (
        "live_20260509T120000Z",  # file_name
        "2026-05-09T12:00:00+00:00",  # captured_at
        "平日_12時00分_系統1",  # trip_id
        "平日",  # service_type
        "12:00",  # scheduled_time
        "1",  # route_code
        1,  # stop_sequence
        0,  # dep_delay
    )

    with patch("pipeline.ingest.safe_urlopen", return_value=mock_resp) as mock_safe_urlopen:
        with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_strategy_row]):
            with patch("pipeline.ingest.recent_file_name_exists", return_value=False):
                with patch("pipeline.ingest.insert_updates", return_value=1):
                    result = ingest_live(1, mock_conn, MagicMock())

    mock_safe_urlopen.assert_called_once_with("https://8.8.8.8/feed.pb", timeout=30)
    assert result == 1


def test_ingest_live_skips_duplicate_poll_within_same_second(tmp_path):
    """Two invocations landing in the same second (a double cron poke, or a
    retried BackgroundTask on the cron endpoint) would produce the identical
    second-granularity file_name. A Postgres UNIQUE(agency_id, file_name,
    trip_id, stop_sequence) + ON CONFLICT DO NOTHING would absorb this
    for free; ClickHouse has no equivalent, so ingest_live must check first
    and skip entirely rather than double-insert the same poll."""
    mock_conn = MagicMock()
    # Two fetchone calls: feed_url SELECT, then ingest_strategy SELECT —
    # resolved once in ingest_live and passed through to ingest_live_payload.
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [
        ("https://8.8.8.8/feed.pb",),
        (None,),
    ]
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"fake protobuf data"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.ingest.safe_urlopen", return_value=mock_resp):
        with patch("pipeline.ingest.recent_file_name_exists", return_value=True) as mock_exists:
            with patch("pipeline.strategies.aomori_regex.parse_feed") as mock_parse:
                with patch("pipeline.ingest.insert_updates") as mock_insert:
                    result = ingest_live(1, mock_conn, MagicMock())

    mock_exists.assert_called_once()
    mock_parse.assert_not_called()
    mock_insert.assert_not_called()
    assert result == 0


def _counts(ch_client, agency_id):
    def n(table):
        return ch_client.query(
            f"SELECT count() FROM {table} WHERE agency_id = {{a:UInt16}}", parameters={"a": agency_id}
        ).result_rows[0][0]

    return n("updates_live"), n("updates")


def test_a_live_payload_lands_in_updates_live_only(pg_conn, ch_client, agency_id):
    captured = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    row = ("oracle/20261002/TripUpdate_031500.pb", captured, "平日_12時00分_系統1", "平日", "12:00", "1", 1, 30)
    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[row]):
        assert ingest_live_payload(agency_id, b"raw", captured, row[0], pg_conn, ch_client) == 1
        # A retry is recognised against the table the first copy went to.
        assert ingest_live_payload(agency_id, b"raw", captured, row[0], pg_conn, ch_client) == 0
    assert _counts(ch_client, agency_id) == (1, 0)


def _stored(ch_client, agency_id):
    return ch_client.query(
        "SELECT stop_sequence, dep_delay, arr_delay FROM updates_live WHERE agency_id = {a:UInt16} "
        "ORDER BY stop_sequence",
        parameters={"a": agency_id},
    ).result_rows


def test_a_live_poll_with_early_stops_and_a_sequenceless_stop_is_stored(pg_conn, ch_client, agency_id):
    """A negative int32 delay and a StopTimeUpdate with no stop_sequence are
    both legal GTFS-RT. Neither may fail the insert, which would lose every
    other stop in the poll."""
    pb = trip_update_feed(
        trip_update(
            "平日_12時00分_系統1",
            stop_time_update(1, departure_delay=-30),
            stop_time_update(departure_delay=60),
            stop_time_update(2, departure_delay=45),
        )
    )
    captured = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    assert ingest_live_payload(agency_id, pb, captured, "oracle/20261002/TripUpdate_031500.pb", pg_conn, ch_client) == 2
    assert _stored(ch_client, agency_id) == [(1, -30, None), (2, 45, None)]


def test_a_static_join_live_poll_with_early_stops_and_a_sequenceless_stop_is_stored(pg_conn, ch_client):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('static_join_signed_delay_test', 'http://signed-delay.example.com/feed.pb', 'static_join') "
            "RETURNING agency_id"
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()

    pb = trip_update_feed(
        trip_update(
            "uuid-A",
            stop_time_update(1, departure_delay=-30, arrival_delay=-45),
            stop_time_update(stop_id="S2", departure_delay=60),
            stop_time_update(3, departure_delay=0),
            route_id="R1",
        )
    )
    captured = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    assert ingest_live_payload(aid, pb, captured, "oracle/20261002/TripUpdate_031500.pb", pg_conn, ch_client) == 2
    assert _stored(ch_client, aid) == [(1, -30, -45), (3, 0, None)]
