"""static_fetcher tests with mocked HTTP."""

import json
import pathlib
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import pytest

from pipeline.strategies import direct_url, aomori_index_scrape


# ── direct_url ────────────────────────────────────────────────────────────────


def _mock_response(body: bytes, headers=None):
    m = MagicMock()
    m.read.return_value = body
    m.headers = headers or {}
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_direct_url_persists_new_zip(tmp_path):
    body_current = b"PK\x03\x04current"
    body_latest = b"PK\x03\x04current"  # identical → loads current

    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(body_current, {"Last-Modified": "lm1", "ETag": "et1"}),
        _mock_response(body_latest, {"Last-Modified": "lm1", "ETag": "et1"}),
    ]):
        result = direct_url.fetch(
            agency_id=8,
            static_url="https://example.com/static/8/current_data.zip",
            dest_dir=tmp_path,
        )
    assert result is not None
    assert result.read_bytes() == body_current
    manifest = json.loads((tmp_path / "8" / "_manifest.json").read_text())
    assert manifest["current"]["last_modified"] == "lm1"


def test_direct_url_prefers_latest_when_diff(tmp_path):
    body_current = b"PK\x03\x04current_data"
    body_latest = b"PK\x03\x04latest_data"
    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(body_current, {"Last-Modified": "lm1", "ETag": "et1"}),
        _mock_response(body_latest, {"Last-Modified": "lm2", "ETag": "et2"}),
    ]):
        result = direct_url.fetch(8, "https://example.com/static/8/current_data.zip", tmp_path)
    assert result is not None
    assert result.read_bytes() == body_latest


def test_direct_url_304_returns_none(tmp_path):
    # Pre-seed manifest so cur_sha == manifest['current']['sha256'] after 304
    agency_dir = tmp_path / "8"
    agency_dir.mkdir(parents=True)
    (agency_dir / "_manifest.json").write_text(json.dumps({
        "current": {"sha256": "x", "last_modified": "lm"},
        "latest": {"sha256": "x", "last_modified": "lm"},
    }))
    err = HTTPError("u", 304, "Not Modified", {}, None)
    with patch("urllib.request.urlopen", side_effect=[err, err]):
        result = direct_url.fetch(8, "https://example.com/static/8/current_data.zip", tmp_path)
    assert result is None


def test_direct_url_network_failure_returns_none(tmp_path):
    from urllib.error import URLError
    with patch("urllib.request.urlopen", side_effect=URLError("dns")):
        result = direct_url.fetch(8, "https://example.com/static/8/current_data.zip", tmp_path)
    assert result is None


# ── aomori_index_scrape ───────────────────────────────────────────────────────


def test_aomori_scrape_fetches_resolved_zip(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus-202605.zip">x</a></html>'
    zip_body = b"PK\x03\x04ZIPBODY"

    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(html),
        _mock_response(zip_body),
    ]):
        result = aomori_index_scrape.fetch(
            agency_id=1,
            index_url="https://aomoricitybus.com/opendata/index.html",
            dest_dir=tmp_path,
        )
    assert result is not None
    assert result.read_bytes() == zip_body
    history = (tmp_path / "1" / "fetch_history.csv").read_text()
    assert "gtfs-aomoricitybus-202605.zip" in history


def test_aomori_scrape_no_href_returns_none(tmp_path):
    html = b"<html>no link</html>"
    with patch("urllib.request.urlopen", return_value=_mock_response(html)):
        assert aomori_index_scrape.fetch(1, "https://aomoricitybus.com/opendata/index.html", tmp_path) is None


def test_aomori_scrape_non_zip_body_returns_none(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus.zip">x</a></html>'
    not_zip = b"<html>oops</html>"
    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(html),
        _mock_response(not_zip),
    ]):
        assert aomori_index_scrape.fetch(1, "https://aomoricitybus.com/opendata/index.html", tmp_path) is None
