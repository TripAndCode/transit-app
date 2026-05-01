import pytest
from unittest.mock import MagicMock, patch, call
from pipeline.ingest import ingest_live


def test_ingest_live_raises_when_no_feed_url():
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("",)
    with pytest.raises(ValueError, match="No feed_url"):
        ingest_live(1, mock_conn)


def test_ingest_live_raises_when_agency_not_found():
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    with pytest.raises(ValueError, match="No feed_url"):
        ingest_live(999, mock_conn)


def test_ingest_live_fetches_and_ingests(tmp_path):
    """Test that ingest_live fetches the URL and calls parse_pb with the raw bytes."""
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        "https://example.com/feed.pb",
        None,
    )

    fake_bytes = b"fake protobuf data"

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.ingest.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        with patch("pipeline.ingest.parse_pb", return_value=[]) as mock_parse:
            ingest_live(1, mock_conn)

    mock_urlopen.assert_called_once_with("https://example.com/feed.pb", timeout=30)
    mock_parse.assert_called_once()
    args, kwargs = mock_parse.call_args
    assert args[0] == fake_bytes
