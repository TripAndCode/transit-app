"""The passenger delay record's pure assembly: which trips step 3 offers and
how each is labelled, and the ordered stops step 4 offers with the reading
each one has."""

from pipeline.reports.delay_record import record_stops, record_trips
from pipeline.service_day import clock_to_sec


def _plan(headsign=None, origin="17:42:00", terminal="熊野萩原"):
    return {"headsign": headsign, "origin_departure": origin, "terminal": terminal}


def test_a_trip_the_timetable_runs_that_day_is_labelled_by_its_origin_departure_and_last_stop():
    trips = record_trips(
        [("T1", 30, ("17:45:00", 63900))],
        {"T1": _plan(headsign="旧道・八丁堀経由", origin="17:42:00", terminal="広島駅")},
    )
    assert trips == [
        {
            "trip_id": "T1",
            "departure_sec": 17 * 3600 + 42 * 60,
            "terminal": "広島駅",
            "headsign": "旧道・八丁堀経由",
            "matched": True,
            "observed_stops": 30,
        }
    ]


def test_a_trips_last_stop_is_named_without_its_platform_note():
    """Feeds name a terminal's alighting platform ("青森駅 (降車専用)"); the
    place the trip runs to is the stop itself."""
    (trip,) = record_trips([("T1", 3, ("17:45:00", None))], {"T1": _plan(terminal="青森駅 (降車専用)")})
    assert trip["terminal"] == "青森駅"


def test_a_headsign_that_only_repeats_the_last_stop_is_dropped():
    (trip,) = record_trips([("T1", 3, ("17:45:00", None))], {"T1": _plan(headsign="広島駅", terminal="広島駅")})
    assert (trip["terminal"], trip["headsign"]) == ("広島駅", None)
    (noted,) = record_trips(
        [("T1", 3, ("17:45:00", None))], {"T1": _plan(headsign="青森駅（降車専用）", terminal="青森駅 (降車専用)")}
    )
    assert (noted["terminal"], noted["headsign"]) == ("青森駅", None)


def test_a_trip_the_timetable_does_not_run_that_day_falls_back_to_its_earliest_recorded_time_and_no_headsign():
    """The earliest recorded stop need not be the trip's origin, so nothing
    here is called the origin or names where the trip runs to."""
    (trip,) = record_trips([("T1", 12, ("17:45:00", 63900))], {})
    assert trip == {
        "trip_id": "T1",
        "departure_sec": 63900,
        "terminal": None,
        "headsign": None,
        "matched": False,
        "observed_stops": 12,
    }


def test_an_earliest_recorded_time_without_seconds_is_read_from_its_clock():
    (trip,) = record_trips([("T1", 2, ("17:45", None))], {})
    assert trip["departure_sec"] == 17 * 3600 + 45 * 60


def test_trips_are_ordered_by_departure_with_an_after_midnight_trip_last():
    trips = record_trips(
        [
            ("LATE", 5, ("00:10:00", None)),
            ("NOON", 5, ("12:00:00", 43200)),
            ("MORNING", 5, ("06:00:00", 21600)),
            ("UNKNOWN", 5, (None, None)),
        ],
        {},
    )
    assert [t["trip_id"] for t in trips] == ["MORNING", "NOON", "LATE", "UNKNOWN"]


def test_every_timetable_stop_is_listed_in_order_with_the_reading_each_one_has():
    stops = record_stops(
        [(1, "17:42:00", 63720, 0), (3, "17:50:00", 64200, 480)],
        [(1, "広島バスセンター", "17:42:00"), (2, "紙屋町東", "17:46:00"), (3, "八丁堀", "17:50:00")],
    )
    assert stops == [
        {"stop_sequence": 1, "stop_name": "広島バスセンター", "scheduled_sec": 63720, "dep_delay": 0},
        {"stop_sequence": 2, "stop_name": "紙屋町東", "scheduled_sec": 17 * 3600 + 46 * 60, "dep_delay": None},
        {"stop_sequence": 3, "stop_name": "八丁堀", "scheduled_sec": 64200, "dep_delay": 480},
    ]


def test_a_reading_is_timed_against_its_own_schedule_not_the_timetable_loaded_now():
    """The delay was measured against the schedule the feed recorded with it,
    so the actual time is that schedule plus the delay."""
    (stop,) = record_stops([(1, "17:43:00", 63780, 60)], [(1, "広島駅", "17:42:00")])
    assert stop["scheduled_sec"] == 63780


def test_a_recorded_stop_the_timetable_does_not_hold_is_kept_without_a_name():
    stops = record_stops([(5, "18:00:00", 64800, 120)], [(1, "広島駅", "17:42:00")])
    assert [(s["stop_sequence"], s["stop_name"], s["dep_delay"]) for s in stops] == [
        (1, "広島駅", None),
        (5, None, 120),
    ]


def test_a_trip_the_timetable_does_not_run_lists_only_its_recorded_stops_unnamed():
    stops = record_stops([(4, "17:50:00", 64200, 300), (2, "17:46:00", 63960, 240)], [])
    assert stops == [
        {"stop_sequence": 2, "stop_name": None, "scheduled_sec": 63960, "dep_delay": 240},
        {"stop_sequence": 4, "stop_name": None, "scheduled_sec": 64200, "dep_delay": 300},
    ]


def test_a_readings_clock_is_never_taken_for_its_stops_schedule():
    """One ingest strategy (aomori_regex) writes the trip's start time, read
    from its trip_id, as the clock of every stop and stores no
    scheduled_sec. The timetable gives the stop's own time when it runs the
    trip that day; otherwise the stop has none rather than the trip's start."""
    matched = record_stops([(5, "07:05", None, 77)], [(1, "後潟", "07:05:00"), (5, "後潟大原", "07:07:00")])
    assert matched[1] == {
        "stop_sequence": 5,
        "stop_name": "後潟大原",
        "scheduled_sec": 7 * 3600 + 7 * 60,
        "dep_delay": 77,
    }
    unmatched = record_stops([(5, "07:05", None, 77)], [])
    assert unmatched == [{"stop_sequence": 5, "stop_name": None, "scheduled_sec": None, "dep_delay": 77}]


def test_clock_to_sec_lives_with_the_service_day_rule_it_applies():
    assert clock_to_sec(" 25:30:00 ".strip()) == 25 * 3600 + 30 * 60
    assert clock_to_sec("00:10") == 24 * 3600 + 10 * 60


def test_stops_around_midnight_sit_on_one_axis_whichever_source_times_them():
    """Ingest stores a feed's un-extended "00:10:00" as 600 seconds, while a
    timetable clock before the cutover reads as the next day; both must land
    a day later, or a reading's "00:10" would sit beside a neighbour's
    「翌00:15」."""
    stops = record_stops(
        [(2, "00:10:00", 10 * 60, 60)],
        [(1, "広島駅", "23:55:00"), (2, "紙屋町", "00:10:00"), (3, "八丁堀", "00:15:00")],
    )
    assert [s["scheduled_sec"] for s in stops] == [
        23 * 3600 + 55 * 60,
        24 * 3600 + 10 * 60,
        24 * 3600 + 15 * 60,
    ]


def test_a_feed_that_already_writes_extended_hours_keeps_them():
    stops = record_stops([(2, None, 24 * 3600 + 10 * 60, 60)], [(2, "紙屋町", "24:10:00"), (3, "八丁堀", "24:15:00")])
    assert [s["scheduled_sec"] for s in stops] == [24 * 3600 + 10 * 60, 24 * 3600 + 15 * 60]


def test_an_unmatched_trip_first_recorded_after_midnight_sorts_after_the_evening():
    trips = record_trips([("NIGHT", 2, ("00:10:00", 10 * 60)), ("EVENING", 2, ("23:50:00", 23 * 3600 + 50 * 60))], {})
    assert [(t["trip_id"], t["departure_sec"]) for t in trips] == [
        ("EVENING", 23 * 3600 + 50 * 60),
        ("NIGHT", 24 * 3600 + 10 * 60),
    ]
