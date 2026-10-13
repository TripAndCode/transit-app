"""static_routes' terminal names and loop/both-ways flags, filled by
load_static from GTFS-JP routes_jp.txt and, where that leaves a gap, from the
stops the route's trips start and end at."""

import pathlib
import zipfile

from pipeline.static_loader import load_static
from tests.fixtures.synthetic_gtfs import build_static_zip

_HIRODEN_ZIP = pathlib.Path(__file__).parent.parent / "fixtures" / "hiroden_static.zip"

_STOPS = [
    "SA,広島駅,34.39,132.47",
    "SB,紙屋町,34.39,132.45",
    "SC,宇品,34.35,132.46",
    "SD,,34.40,132.48",
    "SE,  横川駅  ,34.41,132.45",
    "SF,青森駅 ③のりば,40.83,140.73",
    "SG,青森駅 (降車専用),40.83,140.73",
    "SH,青森駅 ②のりば,40.83,140.73",
    "SX,Alpha,34.42,132.44",
    "SY,Bravo,34.43,132.43",
]


def _visits(trip_id: str, *stops: tuple[int, str]) -> list[str]:
    return [f"{trip_id},{seq},{stop_id},08:00:00,08:00:00" for seq, stop_id in stops]


_ROUTE_TRIPS: dict[str, list[tuple[tuple[int, str], ...]]] = {
    # Two trips run 広島駅 -> 宇品 and one runs back: named by the majority, and both ways.
    "R1": [((1, "SA"), (2, "SB"), (3, "SC")), ((1, "SA"), (2, "SC")), ((1, "SC"), (2, "SA"))],
    # stop_times rows out of file order: stop_sequence decides which end is first.
    "R2": [((10, "SC"), (2, "SB"), (9, "SA"))],
    # No trips at all.
    "R3": [],
    # A single-stop trip has no direction to name.
    "R4": [((1, "SA"),)],
    # One trip each way: a tie broken by name.
    "R5": [((1, "SX"), (2, "SY")), ((1, "SY"), (2, "SX"))],
    # Two trips end at an unnamed stop; one starts at a padded name.
    "R6": [((1, "SA"), (2, "SD")), ((1, "SA"), (2, "SD")), ((1, "SE"), (2, "SA"))],
    # Starts and ends at two platforms of one station: a loop.
    "R7": [((1, "SF"), (2, "SB"), (3, "SG"))],
    # Two of three trips start at platforms of one station, which vote together.
    "R8": [((1, "SF"), (2, "SC")), ((1, "SH"), (2, "SC")), ((1, "SB"), (2, "SC"))],
    # No trips; only routes_jp can name it.
    "R9": [],
}

_ROUTES = [f"{route_id},{route_id[1:]}" for route_id in _ROUTE_TRIPS]

_TRIPS = {
    f"{route_id}_T{i}": (route_id, stops) for route_id, trips in _ROUTE_TRIPS.items() for i, stops in enumerate(trips)
}

_NOWHERE = (None, None, None, False, False)

_FALLBACK = {
    "R1": ("広島駅", None, "宇品", False, True),
    "R2": ("紙屋町", None, "宇品", False, False),
    "R3": _NOWHERE,
    "R4": _NOWHERE,
    "R5": ("Alpha", None, "Bravo", False, True),
    "R6": ("横川駅", None, "広島駅", False, False),
    "R7": ("青森駅", None, "青森駅", True, False),
    "R8": ("青森駅", None, "宇品", False, False),
    "R9": _NOWHERE,
}

_PARTIAL_ROUTES_JP = [
    "R1,,JP起点,,JP終点",
    "R2,, 宇品 ,経由Y,",
    "R3,,  ,  ,  ",
    "R5,,,経由Z,",
    "R9,,青森駅 ③のりば,,青森駅（降車専用）",
    "R_UNKNOWN,,どこか,,どこか",
]


def _zip(tmp_path, *, routes_jp_rows=None, filename="terminals_static.zip", with_routes=True):
    return build_static_zip(
        tmp_path,
        stops_rows=_STOPS,
        routes_rows=_ROUTES if with_routes else None,
        trips_rows=[f"{trip_id},{route_id},," for trip_id, (route_id, _) in _TRIPS.items()],
        stop_times_rows=[row for trip_id, (_, stops) in _TRIPS.items() for row in _visits(trip_id, *stops)],
        routes_jp_rows=routes_jp_rows,
        filename=filename,
    )


def _terminals(pg_conn, agency_id) -> dict[str, tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_id, origin_name, via_name, destination_name, is_loop, bidirectional "
            "FROM static_routes WHERE agency_id = %s",
            (agency_id,),
        )
        return {row[0]: tuple(row[1:]) for row in cur.fetchall()}


def test_routes_jp_names_every_hiroden_route(pg_conn, agency_id):
    load_static(str(_HIRODEN_ZIP), agency_id, pg_conn)
    terminals = _terminals(pg_conn, agency_id)

    assert terminals["1471981638"] == ("広島バスセンター", None, "あさひが丘下", False, False)
    assert terminals["101622323"][:4] == ("県庁前", "地方", "仁保車庫前", False)
    # エキまちループ (左回り) and 長の木長迫線 32-2 start and end at the same stop.
    assert terminals["3070848961"] == ("広島駅", None, "広島駅", True, False)
    assert terminals["144501529"] == ("呉駅前", None, "呉駅前", True, False)
    # 3号線 and the イオンモール shuttle run both ways.
    assert terminals["360868713"][3:] == (False, True)
    assert terminals["1870599885"] == ("イオンモール広島府中", None, "広島駅新幹線口", False, True)
    assert len(terminals) == 167
    assert all(origin and destination for origin, _, destination, _, _ in terminals.values())


def test_without_routes_jp_each_route_takes_its_dominant_first_and_last_stop(pg_conn, agency_id, tmp_path):
    load_static(_zip(tmp_path), agency_id, pg_conn)

    assert _terminals(pg_conn, agency_id) == _FALLBACK


def test_routes_jp_wins_and_the_trips_fill_only_the_fields_it_leaves_empty(pg_conn, agency_id, tmp_path):
    load_static(_zip(tmp_path, routes_jp_rows=_PARTIAL_ROUTES_JP), agency_id, pg_conn)

    assert _terminals(pg_conn, agency_id) == {
        **_FALLBACK,
        "R1": ("JP起点", None, "JP終点", False, True),
        # routes_jp names only R2's origin, which its trips reach as their last
        # stop: the other end of that pair is the destination, not a loop.
        "R2": ("宇品", "経由Y", "紙屋町", False, False),
        "R5": ("Alpha", "経由Z", "Bravo", False, True),
        "R9": ("青森駅", None, "青森駅", True, False),
    }


def test_reloading_recomputes_terminals_from_the_feed_now_loaded(pg_conn, agency_id, tmp_path):
    with_jp = _zip(tmp_path, routes_jp_rows=_PARTIAL_ROUTES_JP)
    load_static(with_jp, agency_id, pg_conn)
    first = _terminals(pg_conn, agency_id)
    load_static(with_jp, agency_id, pg_conn)
    assert _terminals(pg_conn, agency_id) == first

    load_static(_zip(tmp_path, filename="without_jp_static.zip"), agency_id, pg_conn)
    assert _terminals(pg_conn, agency_id) == _FALLBACK


def test_a_load_without_routes_txt_still_drops_names_its_feed_no_longer_gives(pg_conn, agency_id, tmp_path):
    load_static(_zip(tmp_path, routes_jp_rows=_PARTIAL_ROUTES_JP), agency_id, pg_conn)

    load_static(_zip(tmp_path, filename="no_routes_static.zip", with_routes=False), agency_id, pg_conn)

    assert _terminals(pg_conn, agency_id) == _FALLBACK


def test_an_unreadable_routes_jp_leaves_the_trips_to_name_every_route(pg_conn, agency_id, tmp_path, caplog):
    zip_path = _zip(tmp_path)
    with zipfile.ZipFile(zip_path, "a") as zf:
        zf.writestr(
            "routes_jp.txt",
            "route_id,route_update_date,origin_stop,via_stop,destination_stop\nR1,,起点,,終点\n".encode("cp932"),
        )

    with caplog.at_level("WARNING", logger="pipeline.route_terminals"):
        load_static(zip_path, agency_id, pg_conn)

    assert "routes_jp.txt unreadable" in caplog.text
    assert _terminals(pg_conn, agency_id) == _FALLBACK
