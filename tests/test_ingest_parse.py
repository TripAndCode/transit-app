import re

from pipeline.ingest import parse_trip_id


def test_parse_trip_id_default_pattern():
    result = parse_trip_id("平日_8時30分_系統5")
    assert result == {"service": "平日", "hour": "8", "minute": "30", "route": "5"}


def test_parse_trip_id_custom_pattern():
    custom = re.compile(r"^(?P<service>.+?)_(?P<hour>\d+)h(?P<minute>\d+)_route(?P<route>\d+)$")
    result = parse_trip_id("weekday_8h30_route5", pattern=custom)
    assert result == {"service": "weekday", "hour": "8", "minute": "30", "route": "5"}


def test_parse_trip_id_no_match_returns_none():
    result = parse_trip_id("invalid_trip_id")
    assert result is None


def test_parse_trip_id_default_is_aomori_pattern():
    # Verify the default still works for Aomori format
    result = parse_trip_id("土日祝_14時05分_系統12")
    assert result is not None
    assert result["service"] == "土日祝"
    assert result["route"] == "12"
