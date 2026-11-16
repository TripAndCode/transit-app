"""The add_agency probe's decision, without a database or a network."""

import urllib.error
import zipfile

import pytest

from pipeline import feed_probe as fp
from pipeline.rt_coverage_reprobe import MIN_SAMPLE
from tests.fixtures.gtfs_rt import (
    header_only_feed,
    stop_time_update,
    trip_update,
    trip_update_feed,
    vehicle_descriptor,
    vehicle_position_entity,
    vehicle_position_feed,
)

TS = 1_760_000_000
TRIPS = frozenset(f"t{i}" for i in range(10))
ONE_AGENCY = (fp.StaticAgency("Test Bus", "Asia/Tokyo"),)
TWO_AGENCIES = (fp.StaticAgency("North Bus", "Asia/Tokyo"), fp.StaticAgency("South Bus", "Asia/Tokyo"))


def _static(agencies=ONE_AGENCY, trips=TRIPS):
    return fp.StaticSummary(agencies, trips, frozenset({"s1", "s2"}))


def _tu(trip_id: str, n_stops: int = 3) -> bytes:
    return trip_update(
        trip_id,
        *(stop_time_update(seq, departure_delay=30, stop_id="s1", schedule_relationship=0) for seq in range(n_stops)),
        schedule_relationship=0,
    )


def _feed(trip_ids, n_stops: int = 3) -> bytes:
    return trip_update_feed(*(_tu(t, n_stops) for t in trip_ids), timestamp=TS)


def _rt(*raws: bytes) -> fp.RtProbe:
    return fp.RtProbe(tuple(fp.measure_poll(r) for r in raws))


def _matching(n_in: int, n_out: int) -> bytes:
    return _feed([f"t{i}" for i in range(n_in)] + [f"x{i}" for i in range(n_out)])


VEHICLES_ONLY = vehicle_position_feed(vehicle_position_entity("t1", vehicle_descriptor("v1")), timestamp=TS)
NO_TRIP_ID = trip_update_feed(trip_update("", stop_time_update(1, departure_delay=0)), timestamp=TS)
AOMORI_IDS = _feed(["平日_08時27分_系統47032", "平日_08時51分_系統35142"])


@pytest.mark.parametrize(
    ("static", "raws", "name", "accept_empty", "verdict", "decided_name"),
    [
        pytest.param(_static(), [_matching(8, 2)], None, False, fp.Verdict.ACCEPT, "Test Bus", id="at-threshold"),
        pytest.param(_static(), [_matching(10, 0)], None, False, fp.Verdict.ACCEPT, "Test Bus", id="all-match"),
        pytest.param(_static(), [_matching(7, 3)], None, False, fp.Verdict.LOW_JOIN, "Test Bus", id="below-threshold"),
        pytest.param(_static(), [AOMORI_IDS], None, False, fp.Verdict.LOW_JOIN, "Test Bus", id="aomori-ids"),
        pytest.param(_static(), [header_only_feed(TS)] * 3, None, False, fp.Verdict.EMPTY, "Test Bus", id="empty"),
        pytest.param(
            _static(), [header_only_feed(TS)], None, True, fp.Verdict.ACCEPT_EMPTY, "Test Bus", id="empty-accepted"
        ),
        pytest.param(
            _static(), [VEHICLES_ONLY], None, True, fp.Verdict.NO_TRIP_UPDATES, "Test Bus", id="vehicles-only"
        ),
        pytest.param(_static(), [NO_TRIP_ID], None, False, fp.Verdict.NO_TRIP_IDS, "Test Bus", id="no-trip-ids"),
        pytest.param(
            _static(TWO_AGENCIES), [_matching(10, 0)], None, False, fp.Verdict.NAME_REQUIRED, None, id="two-agencies"
        ),
        pytest.param(
            _static(TWO_AGENCIES), [_matching(10, 0)], "North", False, fp.Verdict.ACCEPT, "North", id="two-named"
        ),
        pytest.param(
            _static(None), [_matching(10, 0)], None, False, fp.Verdict.NAME_REQUIRED, None, id="no-agency-txt"
        ),
        pytest.param(
            _static(None), [_matching(10, 0)], "Named", False, fp.Verdict.ACCEPT, "Named", id="no-agency-named"
        ),
        pytest.param(
            _static(trips=None), [_matching(10, 0)], "Named", False, fp.Verdict.STATIC_INCOMPLETE, None, id="no-trips"
        ),
    ],
)
def test_decision_table(static, raws, name, accept_empty, verdict, decided_name):
    d = fp.decide(static, _rt(*raws), name=name, accept_empty=accept_empty)
    assert d.verdict is verdict, d.reason
    assert d.name == decided_name
    if d.accepted:
        assert (d.ingest_strategy, d.static_strategy) == ("static_join", "direct_url")
    else:
        assert (d.ingest_strategy, d.static_strategy) == (None, None)


def test_a_refusal_below_the_threshold_states_the_rate_count_and_unmatched_sample():
    d = fp.decide(_static(), _rt(_matching(7, 3)), name=None)
    assert "70% of 10 trip_ids are in trips.txt" in d.reason
    assert "x0, x1, x2" in d.reason
    assert "aomori_regex" in d.reason


def test_a_refusal_whose_trip_ids_follow_the_aomori_pattern_says_so():
    d = fp.decide(_static(), _rt(AOMORI_IDS), name=None)
    assert "2 of them match aomori_regex's trip_id pattern" in d.reason


def test_the_join_rate_counts_distinct_trip_ids_across_every_poll():
    # The busier poll alone is at 67%; across both, 8 of 10 distinct trip_ids match.
    polls = _rt(_feed(["t0", "t1", "t2", "t3", "x0", "x1"]), _feed(["t4", "t5", "t6", "t7"]))
    d = fp.decide(_static(), polls, name=None)
    assert d.verdict is fp.Verdict.ACCEPT, d.reason
    d = fp.decide(_static(), _rt(_feed(["t0", "x0"]), _feed(["t1", "x1"])), name=None)
    assert d.verdict is fp.Verdict.LOW_JOIN


def test_two_agencies_are_listed_when_a_name_is_required():
    d = fp.decide(_static(TWO_AGENCIES), _rt(_matching(10, 0)), name=None)
    assert "North Bus" in d.reason and "South Bus" in d.reason and "--name" in d.reason


def test_an_empty_feed_says_to_probe_in_service_hours():
    d = fp.decide(_static(), _rt(header_only_feed(TS)), name=None)
    assert "probe again in service hours" in d.reason and "--accept-empty" in d.reason


def test_an_accepted_empty_feed_records_no_coverage():
    d = fp.decide(_static(), _rt(header_only_feed(TS)), name=None, accept_empty=True)
    assert not d.record_coverage
    assert "hourly re-probe" in d.reason


def test_coverage_is_recorded_only_from_a_poll_big_enough_to_count():
    thin = _feed(["t0"], n_stops=MIN_SAMPLE - 1)
    assert not fp.decide(_static(), _rt(thin), name=None).record_coverage
    enough = _feed(["t0"], n_stops=MIN_SAMPLE)
    assert fp.decide(_static(), _rt(thin, enough), name=None).record_coverage


def test_polls_that_are_not_feeds_or_fail_are_not_an_empty_feed():
    not_feeds = fp.RtProbe((fp.measure_poll(b"<html>busy</html>"),) * 2)
    assert fp.decide(_static(), not_feeds, name=None, accept_empty=True).verdict is fp.Verdict.NOT_A_FEED
    failed = fp.RtProbe((fp.Poll(error="fetch failed: timed out"),))
    assert fp.decide(_static(), failed, name=None, accept_empty=True).verdict is fp.Verdict.FEED_UNREACHABLE


def test_a_failed_poll_among_measured_ones_does_not_decide():
    rt = fp.RtProbe((fp.Poll(error="fetch failed: reset"), fp.measure_poll(_matching(10, 0))))
    assert fp.decide(_static(), rt, name=None).verdict is fp.Verdict.ACCEPT


def test_measure_poll_counts_entities_by_kind():
    poll = fp.measure_poll(NO_TRIP_ID)
    assert (poll.entities, poll.trip_updates, poll.trip_updates_without_trip_id) == (1, 1, 1)
    poll = fp.measure_poll(VEHICLES_ONLY)
    assert (poll.entities, poll.trip_updates, poll.vehicles) == (1, 0, 1)


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


def _zip(path, files: dict[str, str]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text.encode("utf-8-sig"))
    return path


def test_read_static_summary_reads_a_bom_led_agency_txt(tmp_path):
    z = _zip(
        tmp_path / "g.zip",
        {
            "agency.txt": "agency_id,agency_name,agency_timezone\n1,Test Bus,Asia/Tokyo\n",
            "trips.txt": "route_id,service_id,trip_id\nr,s,t1\nr,s,t2\n",
            "stops.txt": "stop_id,stop_name\ns1,A\n",
        },
    )
    s = fp.read_static_summary(z)
    assert s.agencies == (fp.StaticAgency("Test Bus", "Asia/Tokyo"),)
    assert s.trip_ids == {"t1", "t2"} and s.stop_ids == {"s1"}


def test_read_static_summary_marks_missing_files(tmp_path):
    s = fp.read_static_summary(_zip(tmp_path / "g.zip", {"stops.txt": "stop_id\ns1\n"}))
    assert s.agencies is None and s.trip_ids is None


def test_the_report_redacts_both_urls():
    o = fp.ProbeOutcome(
        "https://feed.test/tu.pb?key=SECRET1",
        "https://static.test/g.zip?key=SECRET2",
        fp.decide(_static(), _rt(_matching(10, 0)), name=None),
        _static(),
        _rt(_matching(10, 0)),
    )
    text = "\n".join(fp.report_lines(o))
    assert "SECRET" not in text
    assert "trip_id join: 10 of 10" in text


def test_a_poll_whose_strings_are_not_utf8_is_not_a_feed_rather_than_a_crash():
    from tests.fixtures.gtfs_rt import _len_delimited

    trip = _len_delimited(1, b"\xff\xfe")
    bad = header_only_feed(TS) + _len_delimited(2, _len_delimited(3, _len_delimited(1, trip)))
    rt = fp.poll_feed("https://feed.test/tu.pb", seconds=0, interval=30, fetch=lambda _url: bad)
    assert rt.polls[0].not_a_feed
    assert fp.decide(_static(), rt, name=None).verdict is fp.Verdict.NOT_A_FEED
