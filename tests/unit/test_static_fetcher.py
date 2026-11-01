"""static_fetcher tests with mocked HTTP.

Patches pipeline.url_guard._opener.open (the point safe_urlopen actually
fetches through), not urllib.request.urlopen directly - both direct_url.py
and aomori_index_scrape.py now route every fetch through safe_urlopen for
SSRF protection (validated URL + re-validated redirects + a size cap), so
patching the raw urlopen wouldn't intercept anything anymore. URLs use
public IP literals (matching tests/unit/test_url_guard.py's hermetic
style) so validate_feed_url's DNS resolution never leaves the sandbox.
"""

import hashlib
import json
import os
import stat
import time
import traceback
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from pipeline.strategies import aomori_index_scrape, direct_url
from pipeline.strategies.errors import StaticFetchError
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


_CURRENT = "https://8.8.8.8/static/8/current_data.zip"


def _http_error(code: int) -> HTTPError:
    return HTTPError("u", code, "status", {}, None)  # type: ignore[arg-type]


def _seed_direct_url_manifest(tmp_path):
    agency_dir = tmp_path / "8"
    agency_dir.mkdir(parents=True)
    (agency_dir / "_manifest.json").write_text(
        json.dumps({"current": {"sha256": "x", "etag": "e"}, "latest": {"sha256": "x", "etag": "e"}})
    )


@pytest.mark.parametrize("code", [404, 500])
def test_direct_url_http_error_on_both_variants_raises_with_the_status(tmp_path, code):
    """A failed fetch is not "nothing newer": an agency with a timetable
    already loaded would otherwise read a dead URL as an up-to-date one."""
    _seed_direct_url_manifest(tmp_path)
    with patch.object(_opener, "open", side_effect=[_http_error(code), _http_error(code)]):
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, _CURRENT, tmp_path)
    assert f"HTTP {code}" in exc.value.reason
    assert "current_data.zip" in exc.value.reason


def test_direct_url_network_failure_raises(tmp_path):
    with patch.object(_opener, "open", side_effect=URLError("dns")):
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, _CURRENT, tmp_path)
    assert "network error" in exc.value.reason
    assert "dns" in exc.value.reason


def test_direct_url_read_timeout_raises_as_a_network_error(tmp_path):
    """A timeout or reset while the body is read surfaces as OSError, not
    URLError, and is the same network failure."""
    stalled = _mock_response(b"")
    stalled.read.side_effect = TimeoutError("timed out")
    with patch.object(_opener, "open", return_value=stalled):
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, _CURRENT, tmp_path)
    assert "TimeoutError" in exc.value.reason


def test_direct_url_ssrf_rejection_raises(tmp_path):
    """FeedURLError (e.g. a redirect into a blocked host, or the size cap) is
    a ValueError, not a URLError, so FETCH_ERRORS must list it explicitly
    rather than rely on it being one; otherwise it escapes as an unexplained
    crash instead of reading as a failed fetch."""
    with patch("pipeline.strategies.direct_url.safe_urlopen", side_effect=FeedURLError("blocked")):
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, _CURRENT, tmp_path)
    assert "rejected" in exc.value.reason
    assert "blocked" in exc.value.reason


def test_direct_url_rejects_unsafe_static_url(tmp_path):
    """static_url must be SSRF-validated exactly like feed_url is: it is just
    as admin-supplied a fetch target. The opener is patched only as a spy, to
    confirm the real blocked-host rejection happens before any fetch is
    attempted."""
    with patch.object(_opener, "open") as opened:
        with pytest.raises(StaticFetchError):
            direct_url.fetch(8, "http://169.254.169.254/latest/meta-data/", tmp_path)
    opened.assert_not_called()


def test_direct_url_ssrf_rejection_does_not_leak_query_string(tmp_path, caplog):
    """static_url routinely carries an API key in the query string (ODPT and
    similar JP GTFS providers). The reason is logged and shown on the admin
    run card, so it must not carry the key."""
    with pytest.raises(StaticFetchError) as exc:
        direct_url.fetch(8, "http://169.254.169.254/latest/meta-data/?acl:consumerKey=SECRET123", tmp_path)
    assert "SECRET123" not in str(exc.value)
    assert "SECRET123" not in caplog.text


def test_direct_url_http_error_reason_does_not_leak_query_string(tmp_path):
    with patch.object(_opener, "open", side_effect=[_http_error(403), _http_error(403)]):
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, f"{_CURRENT}?key=SECRET123", tmp_path)
    assert "SECRET123" not in str(exc.value)


def test_direct_url_latest_failing_beside_a_current_304_is_still_no_change(tmp_path, caplog):
    """latest.zip is derived from the configured URL, and not every host
    publishes one: its failure alone says nothing about the timetable."""
    _seed_direct_url_manifest(tmp_path)
    with patch.object(_opener, "open", side_effect=[_http_error(304), _http_error(404)]):
        assert direct_url.fetch(8, _CURRENT, tmp_path) is None
    assert "latest.zip" in caplog.text
    assert "HTTP 404" in caplog.text


def test_direct_url_latest_failing_beside_a_new_current_loads_current(tmp_path):
    body = b"PK\x03\x04current"
    with patch.object(_opener, "open", side_effect=[_mock_response(body), _http_error(404)]):
        result = direct_url.fetch(8, _CURRENT, tmp_path)
    assert result is not None
    assert result.read_bytes() == body


def test_direct_url_configured_url_failing_beside_a_latest_304_raises(tmp_path):
    """The configured URL is the agency's timetable source: a dead one is a
    failure even when the derived latest.zip has nothing newer."""
    _seed_direct_url_manifest(tmp_path)
    with patch.object(_opener, "open", side_effect=[_http_error(404), _http_error(304)]):
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, _CURRENT, tmp_path)
    assert "HTTP 404" in exc.value.reason
    assert "current_data.zip" in exc.value.reason


def test_direct_url_configured_url_failing_beside_a_new_latest_loads_latest(tmp_path, caplog):
    _seed_direct_url_manifest(tmp_path)
    body = b"PK\x03\x04latest"
    with patch.object(_opener, "open", side_effect=[_http_error(404), _mock_response(body)]):
        result = direct_url.fetch(8, _CURRENT, tmp_path)
    assert result is not None
    assert result.read_bytes() == body
    assert "current_data.zip" in caplog.text


def test_direct_url_configured_url_failing_beside_a_latest_already_loaded_raises(tmp_path):
    """latest.zip answering with the bytes that already loaded says nothing
    about the configured URL, which still failed."""
    body = b"PK\x03\x04loaded"
    agency_dir = tmp_path / "8"
    agency_dir.mkdir(parents=True)
    loaded = {"sha256": hashlib.sha256(body).hexdigest()}
    (agency_dir / "_manifest.json").write_text(json.dumps({"current": loaded, "latest": loaded}))
    with patch.object(_opener, "open", side_effect=[_http_error(500), _mock_response(body)]):
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, _CURRENT, tmp_path)
    assert "HTTP 500" in exc.value.reason
    assert "current_data.zip" in exc.value.reason
    assert "latest.zip" not in exc.value.reason
    assert not list(agency_dir.glob("_tmp_*"))


_DEAD_RUN_TEMPS = ("_tmp_current.zip", "_tmp_latest.zip", "_tmp_current_deadrun1.zip")


@pytest.mark.parametrize("outcome", ["nothing newer", "new zip", "failed"])
def test_direct_url_never_touches_a_temp_file_it_did_not_create(tmp_path, outcome):
    """A recent temp file another run left, dead or still downloading, is
    neither read as this run's download nor removed; this run's own are gone
    when it ends, however it ends."""
    _seed_direct_url_manifest(tmp_path)
    agency_dir = tmp_path / "8"
    for name in _DEAD_RUN_TEMPS:
        (agency_dir / name).write_bytes(b"PK\x03\x04half-written")
    body = b"PK\x03\x04new"
    answers = {
        "nothing newer": [_http_error(304), _http_error(304)],
        "new zip": [_mock_response(body), _http_error(304)],
        "failed": [_http_error(404), _http_error(404)],
    }[outcome]
    with patch.object(_opener, "open", side_effect=answers):
        if outcome == "failed":
            with pytest.raises(StaticFetchError):
                direct_url.fetch(8, _CURRENT, tmp_path)
        else:
            result = direct_url.fetch(8, _CURRENT, tmp_path)
            assert (result.read_bytes() if result else None) == (body if outcome == "new zip" else None)
    assert sorted(p.name for p in agency_dir.glob("_tmp_*")) == sorted(_DEAD_RUN_TEMPS)


def test_direct_url_sweeps_a_temp_file_older_than_any_fetch(tmp_path):
    """A temp file older than any fetch can run is a killed run's, and is
    removed; a fresh one may be a live run's, and stays."""
    _seed_direct_url_manifest(tmp_path)
    agency_dir = tmp_path / "8"
    stale, fresh = agency_dir / "_tmp_current_killed.zip", agency_dir / "_tmp_latest_live.zip"
    for path in (stale, fresh):
        path.write_bytes(b"PK\x03\x04half-written")
    old = time.time() - direct_url.STALE_TEMP_AGE_S - 60
    os.utime(stale, (old, old))
    with patch.object(_opener, "open", side_effect=[_http_error(304), _http_error(304)]):
        assert direct_url.fetch(8, _CURRENT, tmp_path) is None
    assert not stale.exists()
    assert fresh.exists()


def test_direct_url_persisted_zip_is_readable_like_any_other_file(tmp_path):
    """The persisted zip gets the mode an ordinary write gives it, not the
    owner-only mode of the temp file it was downloaded into."""
    _seed_direct_url_manifest(tmp_path)
    with patch.object(_opener, "open", side_effect=[_mock_response(b"PK\x03\x04new"), _http_error(304)]):
        result = direct_url.fetch(8, _CURRENT, tmp_path)
    umask = os.umask(0)
    os.umask(umask)
    assert result is not None
    assert stat.S_IMODE(result.stat().st_mode) == 0o666 & ~umask


def test_direct_url_a_fetch_running_beside_this_one_does_not_take_its_download(tmp_path):
    """Two fetches of one agency that nothing serializes (two command-line
    runs, or one beside an admin press) share the agency directory. One that
    starts and ends while this one is mid-fetch must leave this one's
    download in place."""
    _seed_direct_url_manifest(tmp_path)
    body = b"PK\x03\x04new"
    opened = 0

    def _open(req, **_kwargs):
        nonlocal opened
        opened += 1
        if opened == 1:
            return _mock_response(body)
        if opened == 2:
            assert direct_url.fetch(8, _CURRENT, tmp_path) is None
        raise _http_error(304)

    with patch.object(_opener, "open", side_effect=_open):
        result = direct_url.fetch(8, _CURRENT, tmp_path)
    assert result is not None
    assert result.read_bytes() == body
    assert not list((tmp_path / "8").glob("_tmp_*"))


def _seed_loaded_manifest(tmp_path, body: bytes) -> None:
    """A manifest saying ``body`` is the zip that last loaded, with the
    validators the source answered it with."""
    agency_dir = tmp_path / "8"
    agency_dir.mkdir(parents=True)
    loaded = {"sha256": hashlib.sha256(body).hexdigest(), "etag": "et1", "last_modified": "lm1"}
    (agency_dir / "_manifest.json").write_text(json.dumps({"current": loaded, "latest": loaded}))


def _conditional_headers(opened) -> list[tuple[str | None, str | None]]:
    return [
        (call.args[0].get_header("If-none-match"), call.args[0].get_header("If-modified-since"))
        for call in opened.call_args_list
    ]


def test_direct_url_force_fetches_the_bytes_that_last_loaded_without_asking_for_changes(tmp_path):
    """The manifest can outlive the timetable it describes, so a forced fetch
    neither asks the source for changes since it nor compares against it."""
    body = b"PK\x03\x04loaded"
    _seed_loaded_manifest(tmp_path, body)
    with patch.object(_opener, "open", side_effect=[_mock_response(body), _mock_response(body)]) as opened:
        assert direct_url.fetch(8, _CURRENT, tmp_path) is None
    assert _conditional_headers(opened) == [("et1", "lm1"), ("et1", "lm1")]

    with patch.object(
        _opener, "open", side_effect=[_mock_response(body, {"ETag": "et2"}), _mock_response(body)]
    ) as opened:
        result = direct_url.fetch(8, _CURRENT, tmp_path, force=True)
    assert _conditional_headers(opened) == [(None, None), (None, None)]
    assert result is not None
    assert result.read_bytes() == body

    direct_url.record_loaded(8, tmp_path, result)
    manifest = json.loads((tmp_path / "8" / "_manifest.json").read_text())
    assert manifest["current"]["etag"] == "et2"
    assert manifest["current"]["sha256"] == hashlib.sha256(body).hexdigest()


def test_direct_url_force_loads_latest_when_the_configured_url_fails(tmp_path):
    """Forced, a 200 is a zip to load even when it holds the bytes that last
    loaded, so latest.zip answering stands in for a failed configured URL."""
    body = b"PK\x03\x04loaded"
    _seed_loaded_manifest(tmp_path, body)
    with patch.object(_opener, "open", side_effect=[_http_error(500), _mock_response(body)]):
        result = direct_url.fetch(8, _CURRENT, tmp_path, force=True)
    assert result is not None
    assert result.read_bytes() == body


def test_direct_url_force_that_downloads_nothing_persists_nothing(tmp_path):
    """A source that answers 304 to a request with no validators sent nothing
    to load, and an empty temp file is never persisted as a zip."""
    _seed_loaded_manifest(tmp_path, b"PK\x03\x04loaded")
    with patch.object(_opener, "open", side_effect=[_http_error(304), _http_error(404)]):
        assert direct_url.fetch(8, _CURRENT, tmp_path, force=True) is None
    assert not list((tmp_path / "8").glob("gtfs_static_*.zip"))


_NOT_HTTP_URLS = [
    "8.8.8.8/static/8/current_data.zip?key=SECRET123",
    "user:SECRET123@8.8.8.8/static/8/current_data.zip",
    "https://8.8.8.8:port/static/8/current_data.zip?key=SECRET123",
    "https://[8.8.8.8/static/8/current_data.zip?key=SECRET123",
]


def _rendered(exc: BaseException) -> str:
    """Everything a log line or a traceback could show of ``exc``."""
    return "".join(traceback.format_exception(exc))


@pytest.mark.parametrize("url", _NOT_HTTP_URLS)
def test_direct_url_static_url_that_is_not_an_http_url_raises_without_echoing_it(tmp_path, url):
    """A value that does not parse as an http(s) URL cannot be redacted
    reliably, and may still carry a key, so the reason names it without
    quoting it."""
    with patch.object(_opener, "open") as opened:
        with pytest.raises(StaticFetchError) as exc:
            direct_url.fetch(8, url, tmp_path)
    opened.assert_not_called()
    assert exc.value.reason == "the timetable URL is not a valid http(s) URL"
    assert "SECRET123" not in _rendered(exc.value)


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


def test_aomori_scrape_force_returns_the_bytes_that_last_loaded(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus-202605.zip">x</a></html>'
    body = b"PK\x03\x04SCHEDULE-A"
    sha = hashlib.sha256(body).hexdigest()
    agency_dir = tmp_path / "1"
    agency_dir.mkdir()
    (agency_dir / "_manifest.json").write_text(json.dumps({"sha256": sha}))

    def fetch(**kwargs):
        with patch.object(_opener, "open", side_effect=[_mock_response(html), _mock_response(body)]):
            return aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path, **kwargs)

    assert fetch() is None
    result = fetch(force=True)
    assert result is not None
    assert result.read_bytes() == body
    aomori_index_scrape.record_loaded(1, tmp_path, result)
    assert json.loads((agency_dir / "_manifest.json").read_text()) == {"sha256": sha}


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
    with pytest.raises(StaticFetchError) as exc:
        aomori_index_scrape.fetch(1, "http://169.254.169.254/latest/meta-data/?key=SECRET123", tmp_path)
    assert "rejected" in exc.value.reason
    assert "SECRET123" not in str(exc.value)


@pytest.mark.parametrize("code", [404, 500])
def test_aomori_scrape_index_http_error_raises_with_the_status(tmp_path, code):
    with patch.object(_opener, "open", side_effect=HTTPError("u", code, "status", {}, None)):  # type: ignore[arg-type]
        with pytest.raises(StaticFetchError) as exc:
            aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path)
    assert f"HTTP {code}" in exc.value.reason
    assert "index.html" in exc.value.reason


def test_aomori_scrape_zip_network_error_raises(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus-202605.zip">x</a></html>'
    with patch.object(_opener, "open", side_effect=[_mock_response(html), URLError("connection refused")]):
        with pytest.raises(StaticFetchError) as exc:
            aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path)
    assert "network error" in exc.value.reason
    assert "gtfs-aomoricitybus-202605.zip" in exc.value.reason


def test_aomori_scrape_index_page_without_a_timetable_link_raises(tmp_path):
    """A page that no longer links the zip is a moved or redesigned source,
    not a timetable that has not changed."""
    html = b"<html>no link</html>"
    with patch.object(_opener, "open", return_value=_mock_response(html)):
        with pytest.raises(StaticFetchError) as exc:
            aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path)
    assert "link" in exc.value.reason


def test_aomori_scrape_non_zip_body_raises(tmp_path):
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
        with pytest.raises(StaticFetchError) as exc:
            aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path)
    assert "not a zip" in exc.value.reason


def test_aomori_scrape_rejects_zip_url_scraped_to_a_blocked_host(tmp_path):
    """The zip_url isn't admin-configured - it's scraped out of index_url's
    own HTML - so it must be validated exactly like any other fetch target,
    scheme included, before anything is fetched from it."""
    html = b'<html><a href="http://169.254.169.254/latest/meta-data/gtfs-aomoricitybus.zip">x</a></html>'
    with patch.object(_opener, "open", return_value=_mock_response(html)) as opened:
        with pytest.raises(StaticFetchError) as exc:
            aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path)
    assert opened.call_count == 1
    assert "rejected" in exc.value.reason


@pytest.mark.parametrize("url", _NOT_HTTP_URLS)
def test_aomori_scrape_index_url_that_is_not_an_http_url_raises_without_echoing_it(tmp_path, url):
    with patch.object(_opener, "open") as opened:
        with pytest.raises(StaticFetchError) as exc:
            aomori_index_scrape.fetch(1, url, tmp_path)
    opened.assert_not_called()
    assert exc.value.reason == "the timetable URL is not a valid http(s) URL"
    assert "SECRET123" not in _rendered(exc.value)


def test_aomori_scrape_zip_link_that_is_not_an_http_url_raises(tmp_path):
    html = b'<html><a href="https://user:SECRET123@[8.8.8.8/downloads/gtfs-aomoricitybus.zip">x</a></html>'
    with patch.object(_opener, "open", return_value=_mock_response(html)) as opened:
        with pytest.raises(StaticFetchError) as exc:
            aomori_index_scrape.fetch(1, "https://8.8.8.8/opendata/index.html", tmp_path)
    assert opened.call_count == 1
    assert exc.value.reason == "the timetable link on the index page is not a valid http(s) URL"
    assert "SECRET123" not in _rendered(exc.value)


# ── refresh_static ───────────────────────────────────────────────────────────


def test_refresh_static_records_a_zip_only_once_it_has_loaded(tmp_path, monkeypatch):
    from pipeline import static_fetcher

    class _Strategy:
        recorded: list = []

        @staticmethod
        def fetch(agency_id, url, dest_dir, *, force=False):
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
        def fetch(agency_id, url, dest_dir, *, force=False):
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


class _ManifestSaysUnchanged:
    """A source whose bytes match the manifest: only a forced fetch returns a zip."""

    def __init__(self, zip_path):
        self.zip_path = zip_path
        self.forced: list[bool] = []

    def fetch(self, agency_id, url, dest_dir, *, force=False):
        self.forced.append(force)
        return self.zip_path if force else None

    def record_loaded(self, agency_id, dest_dir, zip_path):
        pass


@pytest.mark.parametrize(
    ("has_timetable", "force", "loads"),
    [
        pytest.param(False, False, True, id="no timetable loaded forces the fetch"),
        pytest.param(True, False, False, id="a loaded timetable keeps the manifest's answer"),
        pytest.param(True, True, True, id="force=True forces the fetch"),
    ],
)
def test_refresh_static_forces_the_fetch_when_asked_or_when_no_timetable_is_loaded(
    tmp_path, monkeypatch, has_timetable, force, loads
):
    """The manifest can outlive the timetable tables, so its "nothing newer"
    holds only for an agency that still has a timetable loaded."""
    from pipeline import db, static_fetcher

    strategy = _ManifestSaysUnchanged(tmp_path / "gtfs_static_20261015.zip")
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("https://8.8.8.8/x.zip", "direct_url")
    monkeypatch.setattr(static_fetcher, "get_static_strategy", lambda _name: strategy)
    monkeypatch.setattr(db, "_static_loaded", lambda _conn, _agency_id: has_timetable)
    loaded: list[str] = []
    monkeypatch.setattr(static_fetcher, "load_static", lambda path, _agency_id, _conn: loaded.append(path))

    result = static_fetcher.refresh_static(8, conn, tmp_path, force=force)

    assert strategy.forced == [loads]
    assert result == (strategy.zip_path if loads else None)
    assert loaded == ([str(strategy.zip_path)] if loads else [])


@pytest.mark.parametrize("has_timetable", [False, True], ids=["a load", "nothing newer"])
def test_refresh_static_forgets_the_cached_calendar_only_after_a_load(tmp_path, monkeypatch, has_timetable):
    """Reports read day types from a cache, and a load replaces the calendar
    they come from; a reload that found nothing newer changes nothing."""
    from pipeline import day_types, db, static_fetcher

    strategy = _ManifestSaysUnchanged(tmp_path / "gtfs_static_20261015.zip")
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("https://8.8.8.8/x.zip", "direct_url")
    monkeypatch.setattr(static_fetcher, "get_static_strategy", lambda _name: strategy)
    monkeypatch.setattr(db, "_static_loaded", lambda _conn, _agency_id: has_timetable)
    monkeypatch.setattr(static_fetcher, "load_static", lambda *_args: None)
    cleared: list[bool] = []
    monkeypatch.setattr(day_types, "invalidate_day_types", lambda: cleared.append(True))

    static_fetcher.refresh_static(8, conn, tmp_path)

    assert cleared == ([] if has_timetable else [True])


@pytest.mark.parametrize("has_timetable", [False, True], ids=["a load", "nothing newer"])
def test_refresh_static_forgets_the_cached_service_hours_only_after_a_load(tmp_path, monkeypatch, has_timetable):
    """The Live map judges an empty map by hours read from the calendar and
    stop times a load replaces."""
    from pipeline import db, service_hours, static_fetcher

    strategy = _ManifestSaysUnchanged(tmp_path / "gtfs_static_20261015.zip")
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("https://8.8.8.8/x.zip", "direct_url")
    monkeypatch.setattr(static_fetcher, "get_static_strategy", lambda _name: strategy)
    monkeypatch.setattr(db, "_static_loaded", lambda _conn, _agency_id: has_timetable)
    monkeypatch.setattr(static_fetcher, "load_static", lambda *_args: None)
    cleared: list[bool] = []
    monkeypatch.setattr(service_hours, "invalidate_service_hours", lambda: cleared.append(True))

    static_fetcher.refresh_static(8, conn, tmp_path)

    assert cleared == ([] if has_timetable else [True])


class _SourceSendsNothing:
    """A source that answers 304 even to a request carrying no validators."""

    def __init__(self):
        self.forced: list[bool] = []

    def fetch(self, agency_id, url, dest_dir, *, force=False):
        self.forced.append(force)
        return None

    def record_loaded(self, agency_id, dest_dir, zip_path):
        raise AssertionError("nothing loaded, so nothing is recorded")


def _agency_conn(static_url, static_strategy):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (static_url, static_strategy)
    return conn


@pytest.mark.parametrize("force", [False, True])
def test_refresh_static_fails_when_an_agency_with_no_timetable_still_loads_none(tmp_path, monkeypatch, force):
    """ "Nothing newer" for an agency with no timetable leaves it with nothing
    to run against, which the run card has to show as a failure."""
    from pipeline import db, static_fetcher

    strategy = _SourceSendsNothing()
    monkeypatch.setattr(static_fetcher, "get_static_strategy", lambda _name: strategy)
    monkeypatch.setattr(db, "_static_loaded", lambda _conn, _agency_id: False)
    monkeypatch.setattr(static_fetcher, "load_static", MagicMock(side_effect=AssertionError("nothing to load")))

    with pytest.raises(StaticFetchError) as exc:
        static_fetcher.refresh_static(8, _agency_conn("https://8.8.8.8/x.zip", "direct_url"), tmp_path, force=force)
    assert strategy.forced == [True]
    assert exc.value.reason == "the source sent no timetable, though this agency has none loaded"


@pytest.mark.parametrize(("static_url", "static_strategy"), [(None, "direct_url"), ("https://8.8.8.8/x.zip", None)])
def test_refresh_static_of_an_agency_with_no_source_fails_only_without_a_timetable(
    tmp_path, monkeypatch, static_url, static_strategy
):
    """An agency whose source was cleared keeps the timetable it has, so a
    reload is a no-op; with none loaded it is a failure, not "unchanged"."""
    from pipeline import db, static_fetcher

    has_timetable = {"value": True}
    monkeypatch.setattr(db, "_static_loaded", lambda _conn, _agency_id: has_timetable["value"])
    monkeypatch.setattr(static_fetcher, "get_static_strategy", MagicMock(side_effect=AssertionError("no fetch")))

    assert static_fetcher.refresh_static(8, _agency_conn(static_url, static_strategy), tmp_path) is None

    has_timetable["value"] = False
    with pytest.raises(StaticFetchError) as exc:
        static_fetcher.refresh_static(8, _agency_conn(static_url, static_strategy), tmp_path)
    assert exc.value.reason == "this agency has no timetable URL or fetch method configured"
