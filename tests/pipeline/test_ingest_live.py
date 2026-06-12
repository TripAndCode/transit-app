from unittest.mock import MagicMock, patch

import pytest

from pipeline.ingest import ingest_live


def test_ingest_live_raises_when_no_feed_url():
    mock_conn = MagicMock()
    # feed_url SELECT returns empty string; ValueError is raised before strategy lookup
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [("",)]
    with pytest.raises(ValueError, match="No feed_url"):
        ingest_live(1, mock_conn)


def test_ingest_live_raises_when_agency_not_found():
    mock_conn = MagicMock()
    # First fetchone: agency not found → None triggers ValueError before strategy lookup
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    with pytest.raises(ValueError, match="No feed_url"):
        ingest_live(999, mock_conn)


def test_ingest_live_fetches_and_ingests(tmp_path):
    """Test that ingest_live fetches the URL and calls strategy.parse_feed with raw bytes."""
    mock_conn = MagicMock()
    # Two fetchone calls: (1) feed_url SELECT, (2) ingest_strategy SELECT
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [
        ("https://example.com/feed.pb",),
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

    with patch("pipeline.ingest.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_strategy_row]):
            with patch("pipeline.ingest.psycopg2.extras.execute_batch"):
                result = ingest_live(1, mock_conn)

    mock_urlopen.assert_called_once_with("https://example.com/feed.pb", timeout=30)
    assert result == 1
