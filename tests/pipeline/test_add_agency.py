"""`gtfs_pipeline.py add_agency` end to end, against recorded feeds served by a
fake fetch: no network is reached."""

import email.message
import io
import pathlib
import sys
import urllib.error
import urllib.request
import zipfile

import pytest

import gtfs_pipeline
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
FEED = "https://rt.example.test/trip_updates.pb"
STATIC = "https://gtfs.example.test/feed/current_data.zip"


class _Response:
    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)
        self.headers = email.message.Message()

    def read(self, amt=None):
        return self._body.read() if amt is None else self._body.read(amt)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWeb:
    """Serves one static zip at STATIC and recorded polls at FEED; any other
    URL (direct_url's derived latest.zip among them) is a 404."""

    def __init__(self, static_zip: str | bytes, *polls: str):
        self.static = static_zip if isinstance(static_zip, bytes) else (FIXTURES / static_zip).read_bytes()
        self.polls = [(FIXTURES / p).read_bytes() for p in polls]
        self.fetched: list[str] = []

    def urlopen(self, url_or_request, **_kwargs):
        url = url_or_request.full_url if isinstance(url_or_request, urllib.request.Request) else url_or_request
        self.fetched.append(url)
        if url == STATIC:
            return _Response(self.static)
        if url == FEED:
            return _Response(self.polls[min(self.fetched.count(FEED), len(self.polls)) - 1])
        raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)


@pytest.fixture
def web(monkeypatch, tmp_path):
    """Install a FakeWeb factory and run the command from *tmp_path*, where
    refresh-static keeps its zips."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pipeline.feed_probe.validate_feed_url", lambda _url: None)

    def install(static_zip: str | bytes, *polls: str) -> FakeWeb:
        fake = FakeWeb(static_zip, *polls)
        monkeypatch.setattr("pipeline.strategies.direct_url.safe_urlopen", fake.urlopen)
        monkeypatch.setattr("pipeline.poller.safe_urlopen", fake.urlopen)
        return fake

    return install


def _run(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["gtfs_pipeline.py", "add_agency", *argv])
    try:
        gtfs_pipeline.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def _probed(monkeypatch, *extra: str) -> int:
    return _run(monkeypatch, "--feed-url", FEED, "--static-url", STATIC, "--probe-seconds", "0", *extra)


def _count(pg_conn, sql: str) -> int:
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def _nothing_written(pg_conn, tmp_path) -> None:
    for table in ("agencies", "rt_field_coverage_probes", "static_trips", "pipeline_runs"):
        assert _count(pg_conn, f"SELECT count(*) FROM {table}") == 0, table
    assert not (tmp_path / "raw_archives_static").exists()


def _agency(pg_conn):
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id, agency_name, feed_url, static_url, ingest_strategy, static_strategy FROM agencies"
        )
        return cur.fetchall()


def test_an_accepted_feed_is_added_with_its_strategies_verdict_and_timetable(
    pg_conn, ch_client, web, monkeypatch, capsys
):
    fake = web("hirobus_static.zip", "hirobus_tu.bin")
    assert _probed(monkeypatch) == 0
    assert fake.fetched.count(STATIC) == 1

    [(aid, name, feed, static, ingest, static_strategy)] = _agency(pg_conn)
    assert (name, feed, static) == ("広島バス", FEED, STATIC)
    assert (ingest, static_strategy) == ("static_join", "direct_url")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT field_name, source_feed FROM rt_field_coverage_probes WHERE agency_id = %s", (aid,))
        assert sorted(cur.fetchall()) == sorted((f, FEED) for f in RT_COVERAGE_FIELDS)
        cur.execute("SELECT status FROM pipeline_runs WHERE kind = 'static' AND agency_id = %s", (aid,))
        assert cur.fetchall() == [("ok",)]
    assert _count(pg_conn, f"SELECT count(*) FROM static_trips WHERE agency_id = {aid}") > 0
    out = capsys.readouterr().out
    assert f"agency_id: {aid}" in out
    assert "100% of 871 StopTimeUpdates join stop_times.txt" in out


def test_a_feed_whose_trip_ids_miss_the_timetable_is_refused_and_writes_nothing(
    pg_conn, ch_client, web, monkeypatch, tmp_path, capsys
):
    web("hirobus_static.zip", "aomori_sample.pb")
    assert _probed(monkeypatch) == 1
    _nothing_written(pg_conn, tmp_path)
    out = capsys.readouterr().out
    assert "verdict: low_join: 0% of 34 StopTimeUpdates join stop_times.txt" in out
    assert "aomori_regex" in out


def test_an_empty_feed_is_refused_unless_accepted(pg_conn, ch_client, web, monkeypatch, tmp_path, capsys):
    web("geiyo_static.zip", "geiyo_tu.bin")
    assert _probed(monkeypatch) == 1
    _nothing_written(pg_conn, tmp_path)
    assert "probe again in service hours" in capsys.readouterr().out

    assert _probed(monkeypatch, "--accept-empty") == 0
    [(aid, name, _feed, _static, ingest, static_strategy)] = _agency(pg_conn)
    assert (name, ingest, static_strategy) == ("芸陽バス", "static_join", "direct_url")
    assert _count(pg_conn, "SELECT count(*) FROM rt_field_coverage_probes") == 0
    assert _count(pg_conn, f"SELECT count(*) FROM static_trips WHERE agency_id = {aid}") > 0


def test_a_zip_listing_two_agencies_needs_a_name_before_the_feed_is_polled(
    pg_conn, ch_client, web, monkeypatch, tmp_path, capsys
):
    fake = web("hirokoh_static.zip", "hirokoh_tu.bin")
    assert _probed(monkeypatch) == 1
    _nothing_written(pg_conn, tmp_path)
    assert FEED not in fake.fetched
    out = capsys.readouterr().out
    assert "広島交通" in out and "広交観光" in out and "--name" in out


def test_a_dry_run_reports_an_accepted_feed_and_writes_nothing(pg_conn, ch_client, web, monkeypatch, tmp_path, capsys):
    web("hirobus_static.zip", "hirobus_tu.bin")
    assert _probed(monkeypatch, "--dry-run") == 0
    _nothing_written(pg_conn, tmp_path)
    assert "verdict: accept:" in capsys.readouterr().out


def test_a_feed_url_already_added_is_refused_before_anything_is_fetched(pg_conn, web, monkeypatch):
    fake = web("hirobus_static.zip", "hirobus_tu.bin")
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO agencies (agency_name, feed_url) VALUES ('Old', %s)", (FEED,))
    pg_conn.commit()
    assert _probed(monkeypatch) == 1
    assert fake.fetched == []
    assert _count(pg_conn, "SELECT count(*) FROM agencies") == 1


def test_the_unprobed_form_still_inserts_the_row_as_given(pg_conn, web, monkeypatch):
    fake = web("hirobus_static.zip", "hirobus_tu.bin")
    assert _run(monkeypatch, "--name", "Legacy Bus", "--feed-url", FEED) == 0
    assert fake.fetched == []
    [(_aid, name, feed, static, ingest, static_strategy)] = _agency(pg_conn)
    assert (name, feed, static, ingest, static_strategy) == ("Legacy Bus", FEED, None, None, None)


def test_the_unprobed_form_still_needs_a_name(pg_conn, monkeypatch):
    assert _run(monkeypatch, "--feed-url", FEED) == 2
    assert _count(pg_conn, "SELECT count(*) FROM agencies") == 0


def _with_agency_txt(static_zip: str, agency_txt: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(FIXTURES / static_zip) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = agency_txt.encode() if info.filename == "agency.txt" else src.read(info)
            dst.writestr(info.filename, body)
    return buf.getvalue()


def test_the_measured_zip_is_the_one_loaded_with_its_timezone_and_manifest(
    pg_conn, ch_client, web, monkeypatch, tmp_path
):
    web(
        _with_agency_txt("hirobus_static.zip", "agency_id,agency_name,agency_timezone\n1,Paris Bus,Europe/Paris\n"),
        "hirobus_tu.bin",
    )
    assert _probed(monkeypatch) == 0
    with pg_conn.cursor() as cur:
        cur.execute("SELECT agency_id, agency_name, timezone FROM agencies")
        [(aid, name, tz)] = cur.fetchall()
        cur.execute("SELECT DISTINCT static_version_id FROM static_trips WHERE agency_id = %s", (aid,))
        [(version,)] = cur.fetchall()
    assert (name, tz) == ("Paris Bus", "Europe/Paris")
    agency_dir = tmp_path / "raw_archives_static" / str(aid)
    assert (agency_dir / f"{version}.zip").exists()
    assert (agency_dir / "_manifest.json").exists()


def test_a_timetable_that_fails_to_load_keeps_the_agency_and_says_what_to_run(
    pg_conn, ch_client, web, monkeypatch, caplog
):
    web("hirobus_static.zip", "hirobus_tu.bin")

    def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("pipeline.static_fetcher.load_static", broken)
    assert _probed(monkeypatch) == 1
    [(aid, *_rest)] = _agency(pg_conn)
    assert f"refresh-static --agency-id {aid}" in caplog.text and "disk full" in caplog.text
    assert _count(pg_conn, "SELECT count(*) FROM static_trips") == 0


def test_a_clickhouse_that_cannot_take_the_load_adds_nothing(pg_conn, ch_client, web, monkeypatch, tmp_path, caplog):
    web("hirobus_static.zip", "hirobus_tu.bin")

    def missing(_ch):
        raise RuntimeError("ClickHouse has no static_archive")

    monkeypatch.setattr("pipeline.static_archive.require_tables", missing)
    assert _probed(monkeypatch) == 1
    _nothing_written(pg_conn, tmp_path)
    assert "cannot take a timetable load (ClickHouse has no static_archive)" in caplog.text
