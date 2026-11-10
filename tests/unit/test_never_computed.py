"""What the methodology says is never computed stays impossible to compute.

``docs/methodology.md`` and ``docs/methodology.en.md`` publish, under the
heading in :data:`SECTIONS`, that no figure is made per vehicle or driver,
none identifies a passenger, and none reads a fare. The rule holds because
nothing the app stores could carry such an identifier: no column in the
ClickHouse schema, the columns its bootstrap adds, the Postgres migrations or
the columns ingest writes, and no vehicle descriptor survives decoding a feed.

A column of that kind is a change to the published method. The documents
name every identifier in :data:`NEVER_STORED`, so allowing one means editing
both documents and this list together.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.clickhouse import bootstrap
from pipeline.clickhouse import UPDATE_COLUMNS
from pipeline.strategies import aomori_regex
from pipeline.strategies.static_join import _decode_rows
from tests.fixtures.gtfs_rt import (
    stop_time_update,
    trip_update,
    trip_update_feed,
    vehicle_descriptor,
    vehicle_position_entity,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = {"ja": ROOT / "docs" / "methodology.md", "en": ROOT / "docs" / "methodology.en.md"}
SECTIONS = {"ja": "## 計算しないもの", "en": "## What is never computed"}

# Each identifier the documents name, with the names it stands for. A name
# is read in snake case (:func:`_snake`), and any underscore-delimited run of
# its words that matches counts, so `trip_vehicle_id` and `base_fare` are
# caught and a folded spelling (`vehicleid`) too, while `vehicle_km` (planned
# vehicle-kilometres of a timetable), `ridership_weights` (a route's relative
# weight) and `rider_count` (a count, which identifies no one) are not.
_ID = r"(?:ids?|identifiers?|labels?|refs?|no|numbers?|codes?|names?|keys?|hash(?:es)?|descriptors?)"
NEVER_STORED: dict[str, str] = {
    "vehicle_id": rf"vehicles?_?{_ID}|^vehicles?$",
    "license_plate": r"licen[cs]e_?plates?|plates?(?:_?(?:no|numbers?))?",
    "driver_id": r"(?:driver|crew|staff|employee)[a-z0-9]*",
    "passenger_id": rf"(?:passenger|traveler|traveller|cardholder)[a-z0-9]*|riders?_?(?:{_ID}|cards?|emails?)"
    r"|(?:ic_?)?cards?_?(?:ids?|no|numbers?|hash(?:es)?)",
    "fare": r"fares?[a-z0-9]*",
}
_NEVER = re.compile("|".join(rf"(?:^|_)(?:{pattern})(?=_|$)" for pattern in NEVER_STORED.values()))
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_STRING = re.compile(r"'(?:[^']|'')*'")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _identifiers(sql: str) -> set[str]:
    """Every identifier *sql* names outside its comments and string literals."""
    code = _SQL_STRING.sub("''", _SQL_COMMENT.sub(" ", sql))
    return set(_IDENTIFIER.findall(code))


def _snake(name: str) -> str:
    """*name* in lower snake case: `licensePlate` and `LICENSE_PLATE` read as `license_plate`."""
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def _never_stored(names: set[str]) -> list[str]:
    return sorted(name for name in names if _NEVER.search(_snake(name)))


def _schema_sources() -> dict[str, set[str]]:
    sources = {"db/clickhouse/schema.sql": _identifiers(bootstrap.SCHEMA_PATH.read_text(encoding="utf-8"))}
    for path in sorted((ROOT / "db" / "migrations").glob("*.sql")):
        sources[str(path.relative_to(ROOT))] = _identifiers(path.read_text(encoding="utf-8"))
    sources["db/clickhouse/bootstrap.py _NEW_NULLABLE_COLUMNS"] = {name for name, _ in bootstrap._NEW_NULLABLE_COLUMNS}
    sources["pipeline/clickhouse.py UPDATE_COLUMNS"] = set(UPDATE_COLUMNS)
    return sources


def _section(locale: str) -> str:
    text = DOCS[locale].read_text(encoding="utf-8")
    heading = SECTIONS[locale]
    assert f"\n{heading}\n" in text, f'{DOCS[locale].name} has no "{heading}" section'
    return text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def test_no_schema_or_ingested_column_can_hold_what_is_never_computed():
    found = {source: hits for source, names in _schema_sources().items() if (hits := _never_stored(names))}
    assert not found, (
        f"{found}: {DOCS['ja'].relative_to(ROOT)} (and its English twin) publish, under "
        f'"{SECTIONS["ja"]}" / "{SECTIONS["en"]}", that no figure is made per vehicle or driver, '
        "names a passenger, or reads a fare. A column for one is a change to that published method: "
        "change both documents and NEVER_STORED here first."
    )


@pytest.mark.parametrize("locale", sorted(DOCS))
def test_the_methodology_names_every_identifier_it_rules_out(locale):
    section = _section(locale)
    missing = [name for name in NEVER_STORED if f"`{name}`" not in section]
    assert not missing, f'{DOCS[locale].name}\'s "{SECTIONS[locale]}" section must name {missing}'


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE updates ADD COLUMN vehicle_id LowCardinality(Nullable(String))",
        "CREATE TABLE trips (trip_id TEXT, driver_name TEXT)",
        'CREATE TABLE taps ("passenger_id" TEXT, card_id TEXT)',
        "ALTER TABLE agg_route_daily ADD COLUMN fare_yen INTEGER",
        "CREATE TABLE vehicles (license_plate TEXT)",
    ],
)
def test_the_scan_finds_such_a_column(sql):
    assert _never_stored(_identifiers(sql))


@pytest.mark.parametrize(
    "name",
    ["vehicleId", "vehicleid", "VehicleLabel", "licensePlate", "driverid", "passengerId", "fareAmount", "cardNo"],
)
def test_the_scan_finds_such_a_name_however_it_is_spelt(name):
    assert _never_stored({name}) == [name]


@pytest.mark.parametrize(
    "name",
    ["vehicle_license_plate", "trip_vehicle_id", "assigned_driver_id", "base_fare", "rt_vehicle_id", "vehicle_plate"],
)
def test_the_scan_finds_such_a_name_inside_a_longer_one(name):
    assert _never_stored({name}) == [name]


@pytest.mark.parametrize(
    "name",
    [
        "vehicle_km",
        "vehicleKm",
        "vehicle_km_delivered_pct",
        "ridership_weights",
        "ridershipWeights",
        "user_id",
        "provider_id",
        "rider_count",
    ],
)
def test_the_scan_leaves_a_lookalike_alone(name):
    assert _never_stored({name}) == []


def test_the_scan_leaves_comments_literals_and_lookalikes_alone():
    sql = """
        -- No IC-card or vehicle_id data source exists.
        CREATE TABLE s (vehicle_km DOUBLE PRECISION, metric_type TEXT
            CHECK (metric_type IN ('vehicle_km_delivered_pct', 'fare_box')));  /* driver_id */
        CREATE TABLE ridership_weights (weight NUMERIC);
    """
    assert _never_stored(_identifiers(sql)) == []


_VEHICLE = {"vehicle_id": "BUS-4711", "label": "HIROSHIMA-27", "license_plate": "広島200か4711"}


def _feed_with_vehicles(trip_id: str) -> bytes:
    descriptor = vehicle_descriptor(
        _VEHICLE["vehicle_id"], label=_VEHICLE["label"], license_plate=_VEHICLE["license_plate"]
    )
    feed = trip_update_feed(
        trip_update(
            trip_id,
            stop_time_update(1, departure_delay=30, arrival_delay=20, stop_id="S1"),
            stop_time_update(2, departure_delay=45),
            vehicle=descriptor,
        )
    )
    return feed + vehicle_position_entity(trip_id, descriptor)


def _carries_a_vehicle(rows: list[tuple]) -> list[str]:
    return [value for value in _VEHICLE.values() if any(value in str(field) for row in rows for field in row)]


def test_static_join_ingests_no_vehicle_descriptor():
    rows = list(_decode_rows(_feed_with_vehicles("uuid-A")))
    assert len(rows) == 2
    assert _carries_a_vehicle(rows) == []


def test_aomori_ingests_no_vehicle_descriptor():
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    pb = _feed_with_vehicles("平日_12時00分_系統1")
    rows = aomori_regex.parse_feed(pb, "2026-05-09T12:00:00+09:00", "f.pb", 1, conn)
    assert len(rows) == 2
    assert _carries_a_vehicle(rows) == []
