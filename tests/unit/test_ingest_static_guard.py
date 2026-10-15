"""An ingest strategy that joins realtime rows to the static timetable is
refused, before anything is read or written, for an agency with no timetable
loaded. Without that guard every row would be stored with no schedule and its
file counted as done, so loading the timetable later would never repair it.

DB-free: the agency lookup, the timetable check and every ClickHouse call are
patched, and the ClickHouse calls fail the test if they are reached at all.
"""

from unittest.mock import MagicMock

import pytest

import pipeline.ingest as ingest
from pipeline.ingest import PayloadDecodeError, StaticTimetableMissing
from pipeline.strategies import aomori_regex, static_join
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed


def _must_not_run(name):
    def _fail(*_a, **_k):
        raise AssertionError(f"{name} ran for an agency the guard should have refused")

    return _fail


@pytest.fixture
def archive(tmp_path):
    day = tmp_path / "20260401"
    day.mkdir()
    pb = trip_update_feed(trip_update("uuid-A", stop_time_update(1, departure_delay=30), route_id="R1"))
    (day / "TripUpdate_113700.pb").write_bytes(pb)
    return tmp_path


def _strategy(monkeypatch, name, *, has_timetable):
    calls = []

    def _loaded(_conn, agency_id):
        calls.append(agency_id)
        return has_timetable

    monkeypatch.setattr(ingest, "_resolve_strategy_name", lambda _aid, _conn: name)
    monkeypatch.setattr(ingest, "_static_loaded", _loaded)
    return calls


def _refuse_every_write(monkeypatch):
    for name in ("distinct_file_names", "days_with_source", "insert_updates", "recent_file_name_exists"):
        monkeypatch.setattr(ingest, name, _must_not_run(name))
    monkeypatch.setattr(static_join, "parse_feed", _must_not_run("parse_feed"))


def test_archive_ingest_refuses_a_static_join_agency_with_no_timetable(monkeypatch, archive):
    _strategy(monkeypatch, "static_join", has_timetable=False)
    _refuse_every_write(monkeypatch)

    with pytest.raises(StaticTimetableMissing, match="agency 8") as exc_info:
        ingest.ingest(str(archive), 8, MagicMock(), MagicMock())

    assert exc_info.value.agency_id == 8


def test_the_refusal_is_not_a_value_error():
    """`ingest_live_payload` reads a ValueError from parsing as undecodable
    bytes, which resending cannot fix. A missing timetable is the opposite:
    the same bytes ingest once it is loaded."""
    assert not issubclass(StaticTimetableMissing, ValueError)


def test_live_payload_refuses_a_static_join_agency_with_no_timetable(monkeypatch):
    _strategy(monkeypatch, "static_join", has_timetable=False)
    _refuse_every_write(monkeypatch)

    with pytest.raises(StaticTimetableMissing) as exc_info:
        ingest.ingest_live_payload(8, b"raw", "2026-10-01T00:00:00+00:00", "oracle/x", MagicMock(), MagicMock())

    assert not isinstance(exc_info.value, PayloadDecodeError)


def test_live_payload_refuses_with_a_strategy_name_already_resolved(monkeypatch):
    calls = _strategy(monkeypatch, "aomori_regex", has_timetable=False)
    _refuse_every_write(monkeypatch)

    with pytest.raises(StaticTimetableMissing):
        ingest.ingest_live_payload(
            8, b"raw", "2026-10-01T00:00:00+00:00", "oracle/x", MagicMock(), MagicMock(), strategy_name="static_join"
        )
    assert calls == [8]


def test_a_static_join_agency_with_a_timetable_ingests(monkeypatch, archive):
    _strategy(monkeypatch, "static_join", has_timetable=True)
    monkeypatch.setattr(ingest, "distinct_file_names", lambda *_a, **_k: set())
    monkeypatch.setattr(ingest, "days_with_source", lambda *_a, **_k: set())
    monkeypatch.setattr(static_join, "parse_feed", lambda *_a: [("row",)])
    inserted = []
    monkeypatch.setattr(ingest, "insert_updates", lambda _ch, _aid, rows, **_k: inserted.extend(rows) or len(rows))

    assert ingest.ingest(str(archive), 8, MagicMock(), MagicMock()) == 1
    assert inserted == [("row",)]


def test_a_static_join_live_payload_with_a_timetable_ingests(monkeypatch):
    _strategy(monkeypatch, "static_join", has_timetable=True)
    monkeypatch.setattr(ingest, "recent_file_name_exists", lambda *_a, **_k: False)
    monkeypatch.setattr(static_join, "parse_feed", lambda *_a: [("row",)])
    monkeypatch.setattr(ingest, "insert_updates", lambda _ch, _aid, rows, **_k: len(rows))

    assert ingest.ingest_live_payload(8, b"raw", "2026-10-01T00:00:00+00:00", "oracle/x", MagicMock(), MagicMock()) == 1


def test_aomori_regex_never_consults_the_timetable(monkeypatch, archive):
    """Aomori's trip_id carries its own schedule, so it ingests with no
    timetable loaded, and the check is not even run."""
    calls = _strategy(monkeypatch, "aomori_regex", has_timetable=False)
    monkeypatch.setattr(ingest, "distinct_file_names", lambda *_a, **_k: set())
    monkeypatch.setattr(ingest, "days_with_source", lambda *_a, **_k: set())
    monkeypatch.setattr(ingest, "recent_file_name_exists", lambda *_a, **_k: False)
    monkeypatch.setattr(aomori_regex, "parse_feed", lambda *_a: [("row",)])
    monkeypatch.setattr(ingest, "insert_updates", lambda _ch, _aid, rows, **_k: len(rows))

    assert ingest.ingest(str(archive), 1, MagicMock(), MagicMock()) == 1
    assert ingest.ingest_live_payload(1, b"raw", "2026-10-01T00:00:00+00:00", "oracle/x", MagicMock(), MagicMock()) == 1
    assert calls == []


def test_a_live_pull_is_refused_before_the_feed_is_fetched(monkeypatch):
    _strategy(monkeypatch, "static_join", has_timetable=False)
    _refuse_every_write(monkeypatch)
    monkeypatch.setattr(ingest, "safe_urlopen", _must_not_run("safe_urlopen"))
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("http://feed.example/tu.pb",)

    with pytest.raises(StaticTimetableMissing):
        ingest.ingest_live(8, conn, MagicMock())
