"""The typical-day departures query stays within memory however many services
a day type runs.

A timetable can run hundreds of services on one day type. The query that
files each departure under its day types must not hold a copy of a day type's
service list per departure, or one large operator's timetable outgrows the
server's memory.
"""

from __future__ import annotations

from pipeline import static_archive, timetable_sampling

CONTENT = "many-services"
TRIPS = 2_000
STOPS_PER_TRIP = 10
MEMORY_CAP = 200_000_000


def _seed(ch, agency_id: int) -> None:
    trip = f"toString(intDiv(number, {STOPS_PER_TRIP}))"
    sequence = f"toInt32(number % {STOPS_PER_TRIP})"
    rows = f"numbers({TRIPS * STOPS_PER_TRIP})"
    ch.command(
        f"INSERT INTO {static_archive.SCHEDULE} "
        "(agency_id, content_hash, trip_id, stop_sequence, service_type, scheduled_sec) "
        f"SELECT {agency_id}, '{CONTENT}', {trip}, {sequence}, concat('svc', {trip}), "
        f"toInt32(21600 + number % 3600) FROM {rows}"
    )
    ch.command(
        f"INSERT INTO {static_archive.ARCHIVE} (agency_id, content_hash, trip_id, stop_sequence, stop_id) "
        f"SELECT {agency_id}, '{CONTENT}', {trip}, {sequence}, concat('S', toString(number % {STOPS_PER_TRIP})) "
        f"FROM {rows}"
    )


def test_every_day_type_running_every_service_fits_a_small_memory_cap(ch_client, agency_id):
    _seed(ch_client, agency_id)
    services = [[f"svc{trip}" for trip in range(TRIPS)] for _ in timetable_sampling.DAY_TYPES]
    result = ch_client.query(
        timetable_sampling.DEPARTURE_SECONDS_SQL,
        parameters={"agency": agency_id, "content": CONTENT, "services": services},
        settings={"max_memory_usage": MEMORY_CAP},
    )
    # A trip's last stop departs nowhere.
    departures = TRIPS * (STOPS_PER_TRIP - 1)
    counted = {dt: 0 for dt in range(1, len(timetable_sampling.DAY_TYPES) + 1)}
    for dt, _stop, secs in result.result_rows:
        counted[dt] += len(secs)
    assert counted == dict.fromkeys(counted, departures)
