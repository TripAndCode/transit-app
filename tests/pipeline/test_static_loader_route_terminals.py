"""static_routes' terminal names and loop/both-ways flags, filled by
load_static from GTFS-JP routes_jp.txt and, where that leaves a gap, from the
stops the route's trips start and end at."""

import pathlib

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

_ROUTES = [f"R{n},{n}" for n in range(1, 10)]


def _visits(trip_id: str, *stops: tuple[int, str]) -> list[str]:
    return [f"{trip_id},{seq},{stop_id},08:00:00,08:00:00" for seq, stop_id in stops]


# R1: two trips run 広島駅 -> 宇品 and one runs back: named by the majority,
#     and both ways.
# R2: one trip whose stop_times rows are out of order; stop_sequence decides.
# R3: no trips at all. R4: a single-stop trip, which has no direction.
# R5: one trip each way, a tie broken by name.
# R6: two trips end at an unnamed stop and one starts at a padded name.
# R7: starts and ends at two platforms of one station: a loop.
# R8: two of three trips start at platforms of one station, which vote together.
# R9: no trips; only routes_jp can name it.
_TRIPS = {
    "T1": ("R1", ((1, "SA"), (2, "SB"), (3, "SC"))),
    "T2": ("R1", ((1, "SA"), (2, "SC"))),
    "T3": ("R1", ((1, "SC"), (2, "SA"))),
    "T4": ("R2", ((10, "SC"), (2, "SB"), (9, "SA"))),
    "T5": ("R4", ((1, "SA"),)),
    "T6": ("R5", ((1, "SX"), (2, "SY"))),
    "T7": ("R5", ((1, "SY"), (2, "SX"))),
    "T8": ("R6", ((1, "SA"), (2, "SD"))),
    "T9": ("R6", ((1, "SA"), (2, "SD"))),
    "T10": ("R6", ((1, "SE"), (2, "SA"))),
    "T11": ("R7", ((1, "SF"), (2, "SB"), (3, "SG"))),
    "T12": ("R8", ((1, "SF"), (2, "SC"))),
    "T13": ("R8", ((1, "SH"), (2, "SC"))),
    "T14": ("R8", ((1, "SB"), (2, "SC"))),
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
    "R2,, 起点X ,経由Y,",
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

    assert terminals["1471981638"][:4] == ("広島バスセンター", None, "あさひが丘下", False)
    assert terminals["101622323"][:4] == ("県庁前", "地方", "仁保車庫前", False)
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
        "R2": ("起点X", "経由Y", "宇品", False, False),
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
