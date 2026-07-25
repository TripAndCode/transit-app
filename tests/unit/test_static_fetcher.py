"""static_fetcher tests with mocked HTTP.

Patches pipeline.url_guard._opener.open (the point safe_urlopen actually
fetches through), not urllib.request.urlopen directly - both direct_url.py
and aomori_index_scrape.py now route every fetch through safe_urlopen for
SSRF protection (validated URL + re-validated redirects + a size cap), so
patching the raw urlopen wouldn't intercept anything anymore. URLs use
public IP literals (matching tests/unit/test_url_guard.py's hermetic
style) so validate_feed_url's DNS resolution never leaves the sandbox.
"""

import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from pipeline.strategies import aomori_index_scrape, direct_url
from pipeline.url_guard import FeedURLError, _opener

# ── direct_url ────────────────────────────────────────────────────────────────


def _mock_response(body: bytes, headers=None):
    m = MagicMock()
    m.read.return_value = body
    m.headers = headers or {}
    m.status = 200
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_direct_url_persists_new_zip(tmp_path):
    body_current = b"PK\x03\x04current"
    body_latest = b"PK\x03\x04current"  # identical → loads current

    with patch.object(
        _opener,
        "open",
        side_effect=[
            _mock_response(body_current, {"Last-Modified": "lm1", "ETag": "et1"}),
            _mock_response(body_latest, {"Last-Modified": "lm1", "ETag": "et1"}),
        ],
    ):
        result = direct_url.fetch(
            agency_id=8,
            static_url="https://8.8.8.8/static/8/current_data.zip",
            dest_dir=tmp_path,
        )
    assert result is not None
    assert result.read_bytes() == body_current
    manifest = json.loads((tmp_path / "8" / "_manifest.json").read_text())
    assert manifest["current"]["last_modified"] == "lm1"


def test_direct_url_prefers_latest_when_diff(tmp_path):
    body_current = b"PK\x03\x04current_data"
    body_latest = b"PK\x03\x04latest_data"
    with patch.object(
        _opener,
        "open",
        side_effect=[
            _mock_response(body_current, {"Last-Modified": "lm1", "ETag": "et1"}),
            _mock_response(body_latest, {"Last-Modified": "lm2", "ETag": "et2"}),
        ],
    ):
        result = direct_url.fetch(8, "https://8.8.8.8/static/8/current_data.zip", tmp_path)
    assert result is not None
    assert result.read_bytes() == body_latest


def test_direct_url_304_returns_none(tmp_path):
    # Pre-seed manifest so cur_sha == manifest['current']['sha256'] after 304
    agency_dir = tmp_path / "8"
    agency_dir.mkdir(parents=True)
    (agency_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "current": {"sha256": "x", "last_modified": "lm"},
                "latest": {"sha256": "x", "last_modified": "lm"},
            }
        )
    )
    err = HTTPError("u", 304, "Not Modified", {}, None)
    with patch.object(_opener, "open", side_effect=[err, err]):
        result = direct_url.fetch(8, "https://8.8.8.8/static/8/current_data.zip", tmp_path)
    assert result is None


def test_direct_url_network_failure_returns_none(tmp_path):
    from urllib.error import URLError

    with patch.object(_opener, "open", side_effect=URLError("dns")):
        result = direct_url.fetch(8, "https://8.8.8.8/static/8/current_data.zip", tmp_path)
    assert result is None


def test_direct_url_ssrf_rejection_degrades_gracefully(tmp_path):
    """FeedURLError (e.g. a redirect into a blocked host, or the size cap)
    must degrade like a network failure - log and return None - not
    propagate and abort the whole agency's static refresh. FeedURLError is
    a ValueError, not a URLError, so it needs its own except clause."""
    with patch("pipeline.strategies.direct_url.safe_urlopen", side_effect=FeedURLError("blocked")):
        result = direct_url.fetch(8, "https://8.8.8.8/static/8/current_data.zip", tmp_path)
    assert result is None


def test_direct_url_rejects_unsafe_static_url(tmp_path):
    """static_url must be SSRF-validated exactly like feed_url is - previously
    it wasn't validated at all. No opener mock here: a real blocked-host
    rejection must happen before any fetch is attempted, and (like a
    network failure) degrades to a no-op rather than raising out of fetch()."""
    result = direct_url.fetch(8, "http://169.254.169.254/latest/meta-data/", tmp_path)
    assert result is None


# ── aomori_index_scrape ───────────────────────────────────────────────────────


def test_aomori_scrape_fetches_resolved_zip(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus-202605.zip">x</a></html>'
    zip_body = b"PK\x03\x04ZIPBODY"

    with patch.object(
        _opener,
        "open",
        side_effect=[
            _mock_response(html),
            _mock_response(zip_body),
        ],
    ):
        result = aomori_index_scrape.fetch(
            agency_id=1,
            index_url="https://8.8.8.8/opendata/index.html",
            dest_dir=tmp_path,
        )
    assert result is not None
    assert result.read_bytes() == zip_body
    history = (tmp_path / "1" / "fetch_history.csv").read_text()
    assert "gtfs-aomoricitybus-202605.zip" in history


def test_aomori_scrape_no_href_returns_none(tmp_path):
    html = b"<html>no link</html>"
    with patch.object(_opener, "open", return_value=_mock_response(html)):
        assert aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path) is None


def test_aomori_scrape_non_zip_body_returns_none(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus.zip">x</a></html>'
    not_zip = b"<html>oops</html>"
    with patch.object(
        _opener,
        "open",
        side_effect=[
            _mock_response(html),
            _mock_response(not_zip),
        ],
    ):
        assert aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path) is None


def test_aomori_scrape_rejects_zip_url_scraped_to_a_blocked_host(tmp_path):
    """The zip_url isn't admin-configured - it's scraped out of index_url's
    own HTML - so it must be validated exactly like any other fetch target.
    Previously it wasn't validated at all, not even the scheme. Degrades
    gracefully (like a network failure) rather than raising out of fetch(),
    matching the sibling except clauses in the same function."""
    html = b'<html><a href="http://169.254.169.254/latest/meta-data/gtfs-aomoricitybus.zip">x</a></html>'
    with patch.object(_opener, "open", return_value=_mock_response(html)):
        assert aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path) is None
