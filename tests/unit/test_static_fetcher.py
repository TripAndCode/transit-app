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

import pytest

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
    # The manifest describes only a zip that loaded, so it waits for the load.
    assert not (tmp_path / "8" / "_manifest.json").exists()
    direct_url.record_loaded(8, tmp_path, result)
    manifest = json.loads((tmp_path / "8" / "_manifest.json").read_text())
    assert manifest["current"]["last_modified"] == "lm1"


def test_direct_url_persisted_manifest_redacts_query_string(tmp_path):
    """static_url routinely carries an API key in the query string (ODPT and
    similar JP GTFS providers) - the persisted manifest must not write it
    to disk in cleartext, even though it's a local file, not a log stream."""
    body = b"PK\x03\x04data"
    with patch.object(_opener, "open", side_effect=[_mock_response(body), _mock_response(body)]):
        result = direct_url.fetch(8, "https://8.8.8.8/static/8/current_data.zip?key=SECRET123", tmp_path)
    assert "SECRET123" not in (tmp_path / "8" / "_manifest.pending.json").read_text()
    direct_url.record_loaded(8, tmp_path, result)
    manifest = json.loads((tmp_path / "8" / "_manifest.json").read_text())
    assert "SECRET123" not in json.dumps(manifest)


def test_direct_url_fetches_a_zip_whose_load_failed_as_new_again(tmp_path):
    """Without record_loaded the next fetch must not call the same bytes "no
    change", or a failed load is never retried until upstream publishes again."""
    body = b"PK\x03\x04data"
    url = "https://8.8.8.8/static/8/current_data.zip"

    def responses():
        return [_mock_response(body, {"ETag": "et1"}), _mock_response(body, {"ETag": "et1"})]

    with patch.object(_opener, "open", side_effect=responses()):
        first = direct_url.fetch(8, url, tmp_path)
    with patch.object(_opener, "open", side_effect=responses()) as opened:
        retried = direct_url.fetch(8, url, tmp_path)
    assert first is not None and retried is not None
    # No conditional headers yet: none came from a zip that loaded.
    assert opened.call_args_list[0].args[0].get_header("If-none-match") is None

    direct_url.record_loaded(8, tmp_path, retried)
    with patch.object(_opener, "open", side_effect=responses()):
        assert direct_url.fetch(8, url, tmp_path) is None


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
    err = HTTPError("u", 304, "Not Modified", {}, None)  # type: ignore[arg-type]
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
    """static_url must be SSRF-validated exactly like feed_url is: it is just
    as admin-supplied a fetch target. No opener mock here: a real blocked-host
    rejection must happen before any fetch is attempted, and (like a
    network failure) degrades to a no-op rather than raising out of fetch()."""
    result = direct_url.fetch(8, "http://169.254.169.254/latest/meta-data/", tmp_path)
    assert result is None


def test_direct_url_ssrf_rejection_log_does_not_leak_query_string(tmp_path, caplog):
    """static_url routinely carries an API key in the query string (ODPT and
    similar JP GTFS providers) - a rejected fetch must not write it to the
    log at ERROR level."""
    result = direct_url.fetch(8, "http://169.254.169.254/latest/meta-data/?acl:consumerKey=SECRET123", tmp_path)
    assert result is None
    assert "SECRET123" not in caplog.text


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


def test_aomori_scrape_reports_no_change_for_the_bytes_that_last_loaded(tmp_path):
    """Reloading identical bytes would rewrite every static table and mint a
    new static_version_id for the same schedule."""
    html = b'<html><a href="downloads/gtfs-aomoricitybus-202605.zip">x</a></html>'
    url = "https://8.8.8.8/opendata/index.html"

    def fetch(body):
        with patch.object(_opener, "open", side_effect=[_mock_response(html), _mock_response(body)]):
            return aomori_index_scrape.fetch(1, url, tmp_path)

    loaded = fetch(b"PK\x03\x04SCHEDULE-A")
    assert loaded is not None
    # Not loaded yet: the same bytes are still new.
    assert fetch(b"PK\x03\x04SCHEDULE-A") is not None
    aomori_index_scrape.record_loaded(1, tmp_path, loaded)
    history_lines = (tmp_path / "1" / "fetch_history.csv").read_text().count("\n")

    assert fetch(b"PK\x03\x04SCHEDULE-A") is None
    assert (tmp_path / "1" / "fetch_history.csv").read_text().count("\n") == history_lines
    assert fetch(b"PK\x03\x04SCHEDULE-B") is not None


def test_aomori_scrape_persisted_history_redacts_scraped_zip_url_query_string(tmp_path):
    """zip_url is scraped out of index_url's own HTML, not admin-set - a
    compromised/spoofed index page could serve an href carrying an API key
    in the query string or userinfo, and that must not land unredacted in
    the persisted fetch_history.csv, mirroring direct_url.py's manifest
    redaction of static_url/latest_url."""
    html = b'<html><a href="https://user:SECRET123@8.8.8.8/downloads/gtfs-aomoricitybus-202605.zip">x</a></html>'
    zip_body = b"PK\x03\x04ZIPBODY"
    with patch.object(_opener, "open", side_effect=[_mock_response(html), _mock_response(zip_body)]):
        result = aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path)
    assert result is not None
    history = (tmp_path / "1" / "fetch_history.csv").read_text()
    assert "SECRET123" not in history
    assert "gtfs-aomoricitybus-202605.zip" in history


def test_aomori_scrape_rejects_unsafe_index_url(tmp_path):
    """index_url itself must be SSRF-validated too, not just the scraped
    zip_url - symmetric with direct_url.py's coverage of both fetch legs.
    No opener mock: the rejection must happen before any fetch is attempted."""
    assert aomori_index_scrape.fetch(1, "http://169.254.169.254/latest/meta-data/", tmp_path) is None


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
    own HTML - so it must be validated exactly like any other fetch target,
    scheme included, before anything is fetched from it. Degrades
    gracefully (like a network failure) rather than raising out of fetch(),
    matching the sibling except clauses in the same function."""
    html = b'<html><a href="http://169.254.169.254/latest/meta-data/gtfs-aomoricitybus.zip">x</a></html>'
    with patch.object(_opener, "open", return_value=_mock_response(html)):
        assert aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path) is None


# ── refresh_static ───────────────────────────────────────────────────────────


def test_refresh_static_records_a_zip_only_once_it_has_loaded(tmp_path, monkeypatch):
    from pipeline import static_fetcher

    class _Strategy:
        recorded: list = []

        @staticmethod
        def fetch(agency_id, url, dest_dir):
            return tmp_path / "gtfs_static_20261001.zip"

        @staticmethod
        def record_loaded(agency_id, dest_dir, zip_path):
            _Strategy.recorded.append(zip_path.name)

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("https://8.8.8.8/x.zip", "direct_url")
    monkeypatch.setattr(static_fetcher, "get_static_strategy", lambda _name: _Strategy)

    def failing_load(*_args):
        raise RuntimeError("constraint violated")

    monkeypatch.setattr(static_fetcher, "load_static", failing_load)
    # A failed load must still propagate to the caller (refresh_all's
    # rollback/failure accounting, or the CLI's single-agency path) rather
    # than being swallowed here — only record_loaded not being called is not
    # enough to prove that on its own.
    with pytest.raises(RuntimeError):
        static_fetcher.refresh_static(8, conn, tmp_path)
    assert _Strategy.recorded == []

    monkeypatch.setattr(static_fetcher, "load_static", lambda *_args: None)
    static_fetcher.refresh_static(8, conn, tmp_path)
    assert _Strategy.recorded == ["gtfs_static_20261001.zip"]


def test_refresh_static_still_reports_success_when_recording_the_load_fails(tmp_path, monkeypatch):
    """record_loaded runs only after load_static has already committed, so its
    own failure must not turn a successful load into a reported failure."""
    from pipeline import static_fetcher

    class _Strategy:
        @staticmethod
        def fetch(agency_id, url, dest_dir):
            return tmp_path / "gtfs_static_20261001.zip"

        @staticmethod
        def record_loaded(agency_id, dest_dir, zip_path):
            raise OSError("disk full")

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("https://8.8.8.8/x.zip", "direct_url")
    monkeypatch.setattr(static_fetcher, "get_static_strategy", lambda _name: _Strategy)
    monkeypatch.setattr(static_fetcher, "load_static", lambda *_args: None)

    result = static_fetcher.refresh_static(8, conn, tmp_path)
    assert result == tmp_path / "gtfs_static_20261001.zip"
