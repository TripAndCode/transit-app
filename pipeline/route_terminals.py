"""Where each static route runs: its terminal and via stop names, and whether
it loops or runs both ways.

``load_route_terminals`` recomputes ``static_routes``' ``origin_name``,
``via_name``, ``destination_name``, ``is_loop`` and ``bidirectional`` for one
agency from the static rows currently in place, so a display needs no per-request
work. GTFS-JP ``routes_jp.txt``, when the zip has it, names all three stops;
an origin or destination it leaves empty (or every one, for a feed without
the file) takes the route's dominant pair: the (first stop, last stop) name
pair most of its trips run. Only ``routes_jp.txt`` names a via point.

The derivation reads each static table on its own and joins in Python. It
runs inside ``load_static``'s transaction, before the planner statistics for
the just-replaced rows are refreshed, and a multi-way join planned on those
stale statistics can take minutes where these single-table reads take
milliseconds.
"""

import csv
import io
import re
import zipfile
from collections import Counter, defaultdict
from typing import NamedTuple

ROUTES_JP = "routes_jp.txt"

# Boarding-position suffixes ("青森駅 ③のりば", "尾道駅前⑦番") and
# parenthesised notes ("(降車専用)", "（北行)", "（旧うすい橋)") name a
# platform, direction or history of a stop, not the place a route runs to.
# A full-width digit followed by 番 is left alone: "あさひが丘３番" is a place.
_PLATFORM = re.compile(r"[①-⑳⓪❶-➓㉑-㉟㊱-㊿](?:番のりば|番乗り場|番|のりば|乗り場|乗場)?")
_PAREN_NOTE = re.compile(r"[(（][^()（）]*[)）]")
_WHITESPACE = re.compile(r"\s+")

# Variants and short workings split a route's trips over several pairs, so the
# way back is compared with the dominant pair rather than with all trips.
_REVERSE_SHARE = (3, 10)


class RouteTerminals(NamedTuple):
    origin_name: str | None
    via_name: str | None
    destination_name: str | None
    is_loop: bool
    bidirectional: bool


Pair = tuple[str, str]


def normalize_stop_name(name: str | None) -> str | None:
    """A stop name without platform or note annotations, or None if nothing is left."""
    if name is None:
        return None
    name = _PAREN_NOTE.sub(" ", name)
    name = _PLATFORM.sub(" ", name)
    return _WHITESPACE.sub(" ", name).strip() or None


def derive_route_terminals(
    routes_jp: tuple[str | None, str | None, str | None] | None,
    trip_pairs: Counter[Pair],
) -> RouteTerminals:
    """One route's terminals from its routes_jp row and its trips' end pairs.

    ``trip_pairs`` counts trips by their (first, last) normalized stop names.
    The dominant pair is the most counted one; a tie goes to the pair that
    sorts first, so reloading the same feed always picks the same one.

    ``is_loop`` means the stored origin and destination are the same name.
    ``bidirectional`` means the trips also run the dominant pair's exact
    reverse, on at least 30% as many trips; it is never set on a loop, whose
    reverse is itself.
    """
    jp_origin, jp_via, jp_destination = (normalize_stop_name(v) for v in routes_jp or (None, None, None))
    dominant: Pair | None = None
    if trip_pairs:
        dominant = min(trip_pairs, key=lambda pair: (-trip_pairs[pair], pair))
    origin = jp_origin or (dominant[0] if dominant else None)
    destination = jp_destination or (dominant[1] if dominant else None)
    is_loop = origin is not None and origin == destination
    bidirectional = False
    if dominant is not None and not is_loop and dominant[0] != dominant[1]:
        reverse = trip_pairs[(dominant[1], dominant[0])]
        share_num, share_den = _REVERSE_SHARE
        bidirectional = share_den * reverse >= share_num * trip_pairs[dominant]
    return RouteTerminals(origin, jp_via, destination, is_loop, bidirectional)


def _blank_to_none(value: str | None) -> str | None:
    return (value or "").strip() or None


def _read_routes_jp(zf: zipfile.ZipFile) -> dict[str, tuple[str | None, str | None, str | None]]:
    """``route_id`` -> raw (origin, via, destination) names from routes_jp.txt.

    The file is keyed by route_id; should one repeat, its last row wins.
    """
    rows: dict[str, tuple[str | None, str | None, str | None]] = {}
    with zf.open(ROUTES_JP) as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            route_id = _blank_to_none(row.get("route_id"))
            if route_id is not None:
                rows[route_id] = (row.get("origin_stop"), row.get("via_stop"), row.get("destination_stop"))
    return rows


def _trip_end_pairs(cur, agency_id: int) -> dict[str, Counter[Pair]]:
    """Per route, how many trips run each (first, last) normalized stop-name pair.

    A single-stop trip has no direction, and a trip whose first or last stop
    has no name cannot name one, so neither is counted.
    """
    cur.execute("SELECT stop_id, stop_name FROM static_stops WHERE agency_id = %s", (agency_id,))
    names = {stop_id: normalize_stop_name(stop_name) for stop_id, stop_name in cur.fetchall()}
    cur.execute("SELECT trip_id, route_id FROM static_trips WHERE agency_id = %s", (agency_id,))
    route_of = dict(cur.fetchall())
    cur.execute(
        "SELECT DISTINCT ON (trip_id) trip_id, stop_sequence, stop_id FROM static_stop_times "
        "WHERE agency_id = %s ORDER BY trip_id, stop_sequence",
        (agency_id,),
    )
    first_visit = {trip_id: (seq, stop_id) for trip_id, seq, stop_id in cur.fetchall()}
    cur.execute(
        "SELECT DISTINCT ON (trip_id) trip_id, stop_sequence, stop_id FROM static_stop_times "
        "WHERE agency_id = %s ORDER BY trip_id DESC, stop_sequence DESC",
        (agency_id,),
    )
    pairs: dict[str, Counter[Pair]] = defaultdict(Counter)
    for trip_id, last_seq, last_stop in cur.fetchall():
        first_seq, first_stop = first_visit[trip_id]
        route_id = route_of.get(trip_id)
        origin, destination = names.get(first_stop), names.get(last_stop)
        if route_id is None or first_seq == last_seq or origin is None or destination is None:
            continue
        pairs[route_id][(origin, destination)] += 1
    return pairs


_UPDATE_SQL = """
UPDATE static_routes r
SET origin_name = v.origin_name, via_name = v.via_name, destination_name = v.destination_name,
    is_loop = v.is_loop, bidirectional = v.bidirectional
FROM unnest(%(route_ids)s::text[], %(origins)s::text[], %(vias)s::text[], %(destinations)s::text[],
            %(loops)s::boolean[], %(bidirectional)s::boolean[])
    AS v(route_id, origin_name, via_name, destination_name, is_loop, bidirectional)
WHERE r.agency_id = %(agency_id)s AND r.route_id = v.route_id
"""


def load_route_terminals(zf: zipfile.ZipFile, names_in_zip: set[str], cur, agency_id: int) -> int:
    """Recompute every route's terminals for ``agency_id``; returns the rows changed.

    Each value is derived afresh rather than kept from the previous load, so a
    load whose zip omits routes.txt (keeping the old route rows) still ends up
    with exactly what this feed's files say. The caller commits.
    """
    routes_jp = _read_routes_jp(zf) if ROUTES_JP in names_in_zip else {}
    cur.execute(
        "SELECT route_id, origin_name, via_name, destination_name, is_loop, bidirectional "
        "FROM static_routes WHERE agency_id = %s",
        (agency_id,),
    )
    stored = {row[0]: RouteTerminals(*row[1:]) for row in cur.fetchall()}
    pairs = _trip_end_pairs(cur, agency_id)
    changed: dict[str, RouteTerminals] = {}
    for route_id, before in stored.items():
        after = derive_route_terminals(routes_jp.get(route_id), pairs.get(route_id, Counter()))
        if after != before:
            changed[route_id] = after
    if changed:
        route_ids = list(changed)
        cur.execute(
            _UPDATE_SQL,
            {
                "agency_id": agency_id,
                "route_ids": route_ids,
                "origins": [changed[r].origin_name for r in route_ids],
                "vias": [changed[r].via_name for r in route_ids],
                "destinations": [changed[r].destination_name for r in route_ids],
                "loops": [changed[r].is_loop for r in route_ids],
                "bidirectional": [changed[r].bidirectional for r in route_ids],
            },
        )
    return len(changed)
