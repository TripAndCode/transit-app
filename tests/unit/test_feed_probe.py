"""The add_agency probe's decision, without a database or a network."""

import urllib.error
import zipfile

import pytest

from pipeline import feed_probe as fp
from pipeline.rt_coverage_reprobe import MIN_SAMPLE
from tests.fixtures.gtfs_rt import (
    _len_delimited,
    header_only_feed,
    stop_time_update,
    trip_update,
    trip_update_feed,
    vehicle_descriptor,
    vehicle_position_entity,
    vehicle_position_feed,
)

TS = 1_760_000_000
SEQS = range(3)
TRIPS = frozenset(f"t{i}" for i in range(10))
KEYS = frozenset((t, s) for t in TRIPS for s in SEQS)
ONE_AGENCY = (fp.StaticAgency("Test Bus", "Asia/Tokyo"),)
TWO_AGENCIES = (fp.StaticAgency("North Bus", "Asia/Tokyo"), fp.StaticAgency("South Bus", "Asia/Tokyo"))


def _static(agencies=ONE_AGENCY, trips=TRIPS, keys=KEYS, **rows):
    counts = {"trips.txt": len(trips), "stops.txt": 2, "stop_times.txt": len(keys)}
    counts.update({name.replace("_txt", ".txt"): n for name, n in rows.items()})
    return fp.StaticSummary(agencies, counts, frozenset(trips), frozenset({"s1", "s2"}), frozenset(keys))


def _tu(trip_id: str, seqs=SEQS, *, stop_id_only: bool = False) -> bytes:
    stus = (
        stop_time_update(None if stop_id_only else seq, departure_delay=30, stop_id="s1", schedule_relationship=0)
        for seq in seqs
    )
    return trip_update(trip_id, *stus, schedule_relationship=0)


def _feed(trip_ids, seqs=SEQS, **kw) -> bytes:
    return trip_update_feed(*(_tu(t, seqs, **kw) for t in trip_ids), timestamp=TS)


def _rt(*raws: bytes) -> fp.RtProbe:
    return fp.RtProbe(tuple(fp.measure_poll(r) for r in raws))


def _matching(n_in: int, n_out: int) -> bytes:
    return _feed([f"t{i}" for i in range(n_in)] + [f"x{i}" for i in range(n_out)])


def _reason(static, rt, **kw) -> str:
    return fp.reason(fp.ProbeOutcome("https://f.test", "https://s.test", fp.decide(static, rt, **kw), static, rt))


VEHICLES_ONLY = vehicle_position_feed(vehicle_position_entity("t1", vehicle_descriptor("v1")), timestamp=TS)
NO_TRIP_ID = trip_update_feed(trip_update("", stop_time_update(1, departure_delay=0)), timestamp=TS)
AOMORI_IDS = _feed(["平日_08時27分_系統47032", "平日_08時51分_系統35142"])
OFFSET_SEQUENCE = _feed(sorted(TRIPS), seqs=range(100, 103))
STOP_ID_ONLY = _feed(sorted(TRIPS), stop_id_only=True)
MOSTLY_NO_TRIP_IDS = trip_update_feed(*([_tu("")] * 8), _tu("t0"), _tu("t1"), timestamp=TS)
NO_STUS = trip_update_feed(trip_update("t0"), timestamp=TS)


@pytest.mark.parametrize(
    ("static", "raws", "name", "accept_empty", "verdict"),
    [
        pytest.param(_static(), [_matching(8, 2)], None, False, fp.Verdict.ACCEPT, id="at-threshold"),
        pytest.param(_static(), [_matching(10, 0)], None, False, fp.Verdict.ACCEPT, id="all-match"),
        pytest.param(_static(), [_matching(7, 3)], None, False, fp.Verdict.LOW_JOIN, id="below-threshold"),
        pytest.param(_static(), [AOMORI_IDS], None, False, fp.Verdict.LOW_JOIN, id="aomori-ids"),
        pytest.param(_static(), [OFFSET_SEQUENCE], None, False, fp.Verdict.LOW_JOIN, id="offset-stop-sequence"),
        pytest.param(_static(), [STOP_ID_ONLY], None, False, fp.Verdict.STOP_ID_ONLY, id="stop-id-only-stus"),
        pytest.param(_static(), [MOSTLY_NO_TRIP_IDS], None, False, fp.Verdict.LOW_JOIN, id="mostly-no-trip-ids"),
        pytest.param(_static(), [NO_TRIP_ID], None, False, fp.Verdict.NO_TRIP_IDS, id="no-trip-ids"),
        pytest.param(_static(), [NO_STUS], None, False, fp.Verdict.NO_STOP_TIME_UPDATES, id="no-stus"),
        pytest.param(
            _static(trips=TRIPS - {"t0", "t1", "t2"}),
            [_matching(10, 0)],
            None,
            False,
            fp.Verdict.LOW_JOIN,
            id="stop-times-without-the-trip",
        ),
        pytest.param(_static(), [header_only_feed(TS)] * 3, None, False, fp.Verdict.EMPTY, id="empty"),
        pytest.param(_static(), [header_only_feed(TS)], None, True, fp.Verdict.ACCEPT_EMPTY, id="empty-accepted"),
        pytest.param(_static(), [VEHICLES_ONLY], None, True, fp.Verdict.NO_TRIP_UPDATES, id="vehicles-only"),
        pytest.param(_static(TWO_AGENCIES), [_matching(10, 0)], None, False, fp.Verdict.NAME_REQUIRED, id="two"),
        pytest.param(_static(TWO_AGENCIES), [_matching(10, 0)], "North", False, fp.Verdict.ACCEPT, id="two-named"),
        pytest.param(_static(None), [_matching(10, 0)], None, False, fp.Verdict.NAME_REQUIRED, id="no-agency-txt"),
        pytest.param(_static(None), [_matching(10, 0)], "Named", False, fp.Verdict.ACCEPT, id="no-agency-named"),
        pytest.param(
            _static(stop_times_txt=None),
            [_matching(10, 0)],
            None,
            False,
            fp.Verdict.STATIC_INCOMPLETE,
            id="no-stop-times",
        ),
        pytest.param(
            _static(stops_txt=None), [_matching(10, 0)], None, False, fp.Verdict.STATIC_INCOMPLETE, id="no-stops"
        ),
        pytest.param(
            _static(stops_txt=0), [_matching(10, 0)], None, False, fp.Verdict.STATIC_INCOMPLETE, id="empty-stops"
        ),
        pytest.param(
            _static(trips_txt=0), [_matching(10, 0)], None, False, fp.Verdict.STATIC_INCOMPLETE, id="empty-trips"
        ),
    ],
)
def test_decision_table(static, raws, name, accept_empty, verdict):
    d = fp.decide(static, _rt(*raws), name=name, accept_empty=accept_empty)
    assert d.verdict is verdict, d
    if d.accepted:
        assert (d.ingest_strategy, d.static_strategy) == ("static_join", "direct_url")
        assert d.name == (name or "Test Bus")
    else:
        assert (d.ingest_strategy, d.static_strategy) == (None, None)


def test_the_join_counts_rows_not_trip_ids():
    j = fp.decide(_static(), _rt(_matching(7, 3)), name=None).join
    assert (j.rows, j.joined, j.trip_ids, j.trip_ids_matched) == (30, 21, 10, 7)
    j = fp.decide(_static(), _rt(MOSTLY_NO_TRIP_IDS), name=None).join
    assert (j.rows, j.joined, j.without_trip_id) == (30, 6, 24)


def test_rows_join_only_on_a_trip_trips_txt_lists():
    j = fp.decide(_static(trips=TRIPS - {"t0"}), _rt(_matching(10, 0)), name=None).join
    assert j.joined == 27


def test_a_low_join_refusal_states_the_row_rate_the_trip_id_rate_and_misses():
    text = _reason(_static(), _rt(_matching(7, 3)), name=None)
    assert "70% of 30 StopTimeUpdates join stop_times.txt on (trip_id, stop_sequence)" in text
    assert "7 of 10 trip_ids (70%) are in trips.txt" in text
    assert "x0 (stop_sequence 0)" in text


def test_matching_trip_ids_with_other_stop_sequences_say_so():
    text = _reason(_static(), _rt(OFFSET_SEQUENCE), name=None)
    assert "0% of 30 StopTimeUpdates" in text and "10 of 10 trip_ids (100%)" in text
    assert "numbers stop_sequence differently" in text


def test_stop_id_only_stop_time_updates_have_their_own_reason():
    text = _reason(_static(), _rt(STOP_ID_ONLY), name=None)
    assert "30 StopTimeUpdates carry stop_id but no stop_sequence; static_join can't join them" in text


def test_trip_id_less_trip_updates_are_named_as_the_cause():
    assert "name no trip_id" in _reason(_static(), _rt(MOSTLY_NO_TRIP_IDS), name=None)


def test_aomori_shaped_trip_ids_point_at_that_strategy():
    assert "2 unmatched trip_ids match aomori_regex's trip_id pattern" in _reason(_static(), _rt(AOMORI_IDS), name=None)


def test_the_join_rate_counts_every_poll():
    # The busier poll alone joins 67% of its rows; across both, 80%.
    polls = _rt(_feed(["t0", "t1", "t2", "t3", "x0", "x1"]), _feed(["t4", "t5", "t6", "t7"]))
    assert fp.decide(_static(), polls, name=None).verdict is fp.Verdict.ACCEPT
    polls = _rt(_feed(["t0", "x0"]), _feed(["t1", "x1"]))
    assert fp.decide(_static(), polls, name=None).verdict is fp.Verdict.LOW_JOIN


def test_two_agencies_are_listed_when_a_name_is_required():
    text = _reason(_static(TWO_AGENCIES), _rt(_matching(10, 0)), name=None)
    assert "North Bus" in text and "South Bus" in text and "--name" in text


def test_an_empty_feed_says_to_probe_in_service_hours():
    text = _reason(_static(), _rt(header_only_feed(TS)), name=None)
    assert "probe again in service hours" in text and "--accept-empty" in text


def test_an_accepted_empty_feed_records_no_coverage():
    assert not fp.decide(_static(), _rt(header_only_feed(TS)), name=None, accept_empty=True).record_coverage


def test_coverage_is_recorded_only_from_a_poll_big_enough_to_count():
    thin_keys = KEYS | {("t0", s) for s in range(MIN_SAMPLE)}
    thin = _feed(["t0"], seqs=range(MIN_SAMPLE - 1))
    assert not fp.decide(_static(keys=thin_keys), _rt(thin), name=None).record_coverage
    enough = _feed(["t0"], seqs=range(MIN_SAMPLE))
    assert fp.decide(_static(keys=thin_keys), _rt(thin, enough), name=None).record_coverage


@pytest.mark.parametrize(
    ("agencies", "name", "timezone"),
    [
        (ONE_AGENCY, None, "Asia/Tokyo"),
        ((fp.StaticAgency("Test Bus", "Europe/Paris"),), None, "Europe/Paris"),
        ((fp.StaticAgency("Test Bus", "Mars/Olympus"),), None, None),
        ((fp.StaticAgency("Test Bus", None),), None, None),
        (TWO_AGENCIES, "North", "Asia/Tokyo"),
        ((fp.StaticAgency("North Bus", "Asia/Tokyo"), fp.StaticAgency("Paris Bus", "Europe/Paris")), "North", None),
    ],
)
def test_the_timezone_comes_from_a_single_valid_agency_txt_row(agencies, name, timezone):
    assert fp.decide(_static(agencies), _rt(_matching(10, 0)), name=name).timezone == timezone


def test_polls_that_are_not_feeds_or_fail_are_not_an_empty_feed():
    not_feeds = fp.RtProbe((fp.measure_poll(b"<html>busy</html>"),) * 2)
    assert fp.decide(_static(), not_feeds, name=None, accept_empty=True).verdict is fp.Verdict.NOT_A_FEED
    failed = fp.RtProbe((fp.Poll(error="fetch failed: timed out"),))
    assert fp.decide(_static(), failed, name=None, accept_empty=True).verdict is fp.Verdict.FEED_UNREACHABLE


def test_a_failed_poll_among_measured_ones_does_not_decide():
    rt = fp.RtProbe((fp.Poll(error="fetch failed: reset"), fp.measure_poll(_matching(10, 0))))
    assert fp.decide(_static(), rt, name=None).verdict is fp.Verdict.ACCEPT


def test_measure_poll_counts_entities_and_unjoinable_rows():
    poll = fp.measure_poll(NO_TRIP_ID)
    assert (poll.entities, poll.trip_updates, poll.trip_updates_without_trip_id, poll.without_trip_id) == (1, 1, 1, 1)
    poll = fp.measure_poll(VEHICLES_ONLY)
    assert (poll.entities, poll.trip_updates, poll.vehicles) == (1, 0, 1)
    poll = fp.measure_poll(STOP_ID_ONLY)
    assert (poll.keys, poll.without_sequence, poll.stop_id_only) == ((), 30, 30)


def test_poll_feed_polls_at_the_interval_for_the_probe_length_and_keeps_failures():
    answers = iter([_matching(10, 0), urllib.error.URLError("down"), _matching(10, 0)])

    def fetch(_url):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    sleeps: list[float] = []
    rt = fp.poll_feed("https://feed.test/tu.pb", seconds=60, interval=30, fetch=fetch, sleep=sleeps.append)
    assert sleeps == [30, 30]
    assert [p.error is None for p in rt.polls] == [True, False, True]


def test_a_poll_whose_strings_are_not_utf8_is_not_a_feed_rather_than_a_crash():
    trip = _len_delimited(1, b"\xff\xfe")
    bad = header_only_feed(TS) + _len_delimited(2, _len_delimited(3, _len_delimited(1, trip)))
    rt = fp.poll_feed("https://feed.test/tu.pb", seconds=0, interval=30, fetch=lambda _url: bad)
    assert rt.polls[0].not_a_feed
    assert fp.decide(_static(), rt, name=None).verdict is fp.Verdict.NOT_A_FEED


def _zip(path, files: dict[str, bytes | str]):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body.encode("utf-8-sig") if isinstance(body, str) else body)
    return path


GOOD = {
    "agency.txt": "agency_id,agency_name,agency_timezone\n1,Test Bus,Asia/Tokyo\n",
    "trips.txt": "route_id,service_id,trip_id\nr,s,t1\nr,s, t2\n",
    "stops.txt": "stop_id,stop_name\ns1,A\n",
    "stop_times.txt": "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
    "t1,01,s1,08:00:00,08:00:00\nt1, 2,s1,08:05:00,08:05:00\n t2,3,s1,08:09:00,08:09:00\n",
}


def test_read_static_summary_reads_values_as_the_loader_stores_them(tmp_path):
    s = fp.read_static_summary(_zip(tmp_path / "g.zip", GOOD))
    assert s.agencies == (fp.StaticAgency("Test Bus", "Asia/Tokyo"),)
    assert s.rows == {"trips.txt": 2, "stops.txt": 1, "stop_times.txt": 3}
    assert s.trip_ids == {"t1", " t2"}  # stored unstripped
    assert s.stop_time_keys == frozenset()


def test_read_stop_time_keys_keeps_only_the_wanted_keys_as_postgres_reads_them(tmp_path):
    # Postgres reads "01" and " 2" as 1 and 2; trip_ids stay unstripped.
    wanted = {("t1", 1), ("t1", 2), ("t2", 3), ("t1", 9)}
    assert fp.read_stop_time_keys(_zip(tmp_path / "g.zip", GOOD), wanted) == {("t1", 1), ("t1", 2)}


@pytest.mark.parametrize(
    ("raw", "value"),
    [("1", 1), ("01", 1), (" 2\t", 2), ("+3", 3), ("-4", -4), ("2147483647", 2147483647)],
)
def test_stop_sequence_reads_what_postgres_int4_reads(raw, value):
    assert fp._stop_sequence(raw) == value


@pytest.mark.parametrize("raw", ["1_0", "", None, "\u30001", "\u0661", "1.0", "2147483648", "0x10"])
def test_stop_sequence_refuses_what_postgres_int4_refuses(raw):
    with pytest.raises(fp.StaticZipError) as err:
        fp._stop_sequence(raw)
    assert (err.value.verdict, err.value.file) == (fp.Verdict.STATIC_UNREADABLE, "stop_times.txt")


@pytest.mark.parametrize("bad", ["1_0", ""])
def test_a_stop_sequence_the_loader_cannot_store_refuses_the_zip(tmp_path, bad):
    files = {**GOOD, "stop_times.txt": f"trip_id,stop_sequence\nt1,1\nt1,{bad}\n"}
    with pytest.raises(fp.StaticZipError) as err:
        fp.read_static_summary(_zip(tmp_path / "g.zip", files))
    assert (err.value.verdict, err.value.file) == (fp.Verdict.STATIC_UNREADABLE, "stop_times.txt")
    assert f"stop_sequence {bad!r} is not an integer" in err.value.detail


def test_a_member_that_fails_its_crc_cannot_be_decompressed(tmp_path):
    path = tmp_path / "g.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for name, body in GOOD.items():
            zf.writestr(name, body.encode())
    data = bytearray(path.read_bytes())
    data[data.index(b"r,s,t1")] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(fp.StaticZipError) as err:
        fp.read_static_summary(path)
    assert (err.value.verdict, err.value.file) == (fp.Verdict.STATIC_UNREADABLE, "trips.txt")
    assert "cannot be decompressed" in err.value.detail


def test_read_static_summary_marks_missing_and_empty_files(tmp_path):
    s = fp.read_static_summary(_zip(tmp_path / "g.zip", {"stops.txt": "stop_id\n"}))
    assert s.agencies is None
    assert s.rows == {"trips.txt": None, "stops.txt": 0, "stop_times.txt": None}


def test_a_member_that_is_not_utf8_is_refused_by_name(tmp_path):
    files = {**GOOD, "stop_times.txt": "trip_id,stop_sequence\nバス,1\n".encode("shift_jis")}
    with pytest.raises(fp.StaticZipError) as err:
        fp.read_static_summary(_zip(tmp_path / "g.zip", files))
    assert (err.value.verdict, err.value.file) == (fp.Verdict.STATIC_UNREADABLE, "stop_times.txt")
    assert "not UTF-8" in err.value.detail


def test_a_member_over_the_decompressed_limit_is_refused_before_it_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "MAX_MEMBER_BYTES", 1000)
    files = {**GOOD, "shapes.txt": "shape_id\n" + "x\n" * 1000}
    with pytest.raises(fp.StaticZipError) as err:
        fp.read_static_summary(_zip(tmp_path / "g.zip", files))
    assert (err.value.verdict, err.value.file) == (fp.Verdict.STATIC_UNAVAILABLE, "shapes.txt")


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    (tmp_path / "g.zip").write_bytes(b"<html>moved</html>")
    with pytest.raises(fp.StaticZipError) as err:
        fp.read_static_summary(tmp_path / "g.zip")
    assert err.value.verdict is fp.Verdict.STATIC_UNAVAILABLE


def test_the_report_redacts_both_urls():
    static, rt = _static(), _rt(_matching(10, 0))
    o = fp.ProbeOutcome(
        "https://feed.test/tu.pb?key=SECRET1",
        "https://static.test/g.zip?key=SECRET2",
        fp.decide(static, rt, name=None),
        static,
        rt,
    )
    text = "\n".join(fp.report_lines(o))
    assert "SECRET" not in text
    assert "100% of 30 StopTimeUpdates join" in text
