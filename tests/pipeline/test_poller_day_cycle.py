"""The standalone poller across UTC day boundaries: its pushes are promoted,
and once both tarballs of the JST day are packed, archive ingest replaces the
promoted day through the ordinary `ingest()` path (pipeline.archive_replace).
Tarballs that waited out an outage are still read by the next run."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from api.range import jst_today
from pipeline import poller
from pipeline.archive_replace import day_sources
from pipeline.collector_push import PUSH_PREFIX, member_name
from pipeline.strategies._pb import _read_ld, _read_varint
from tests.fixtures.gtfs_rt import header_only_feed

_JST = ZoneInfo("Asia/Tokyo")
_DAY = timedelta(days=1)
_RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "aomori_sample.pb"


def _restamped(raw: bytes, at: datetime) -> bytes:
    """The recorded feed with its FeedHeader replaced by one stamped *at*, as a
    live feed polled then would carry."""
    body = bytearray()
    pos = 0
    while pos < len(raw):
        start = pos
        tag, pos = _read_varint(raw, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 2:
            _, pos = _read_ld(raw, pos)
        elif wire == 0:
            _, pos = _read_varint(raw, pos)
        else:
            raise AssertionError(f"unexpected wire type {wire} at top level")
        if field != 1:
            body += raw[start:pos]
    return header_only_feed(int(at.timestamp())) + bytes(body)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime.min.replace(tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def _utc(day: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm, ss), tzinfo=timezone.utc)


def _file_names(ch_client, agency_id: int, day: date, *, live: bool) -> set[str]:
    lo = datetime.combine(day, time(), tzinfo=_JST)
    rows = ch_client.query(
        "SELECT DISTINCT file_name FROM updates WHERE agency_id = {a:UInt16} "
        "AND captured_at >= {lo:DateTime64} AND captured_at < {hi:DateTime64} "
        f"AND {'' if live else 'NOT '}startsWith(file_name, {{p:String}})",
        parameters={"a": agency_id, "lo": lo, "hi": lo + _DAY, "p": PUSH_PREFIX},
    ).result_rows
    return {row[0] for row in rows}


def test_the_poller_archive_replaces_the_day_it_promoted(pg_conn, ch_client, agency_id, tmp_path):
    # JST day D has closed; its two UTC halves are D-1 from 15:00 and D to 15:00.
    d = jst_today() - _DAY
    recorded = _RECORDED.read_bytes()
    clock = _Clock()
    p = poller.Poller(tmp_path, clock=clock, fetch=lambda url: _restamped(recorded, clock.now), reprobe=lambda: None)

    def cycle(at: datetime) -> None:
        clock.now = at
        p.poll_cycle()
        p.maybe_start_daily()
        p.join_background()

    first_half = [_utc(d - _DAY, 16), _utc(d - _DAY, 23, 59, 30)]
    second_half = [_utc(d, 0, 0, 30), _utc(d, 10), _utc(d, 14, 59)]
    for at in [*first_half, second_half[0]]:
        cycle(at)

    # The first UTC day is packed, but alone it does not cover JST day D: the
    # day holds only its promoted pushes.
    assert (tmp_path / str(agency_id) / "rt" / f"{d - _DAY:%Y%m%d}.tar.gz").exists()
    promoted = _file_names(ch_client, agency_id, d, live=True)
    assert promoted == {f"{PUSH_PREFIX}{member_name(at)}" for at in [*first_half, second_half[0]]}
    assert not _file_names(ch_client, agency_id, d, live=False)

    for at in [*second_half[1:], _utc(d, 15, 0, 30), _utc(d + _DAY, 0, 0, 30)]:
        cycle(at)

    assert (tmp_path / str(agency_id) / "rt" / f"{d:%Y%m%d}.tar.gz").exists()
    assert not _file_names(ch_client, agency_id, d, live=True)
    assert _file_names(ch_client, agency_id, d, live=False) == {member_name(at) for at in [*first_half, *second_half]}
    held = day_sources(ch_client, agency_id, d)
    assert held.live_rows == 0 and held.archive_rows > 0
    assert p.fetch_failures == {} and p.push_failures == {}
    assert p.schedule_state.pending == {}
    assert poller.ingested_through(tmp_path / str(agency_id) / "rt") == f"{d:%Y%m%d}"


def test_tarballs_left_by_a_four_day_outage_are_all_ingested(pg_conn, ch_client, agency_id, tmp_path):
    d = jst_today() - _DAY
    rt = poller.rt_dir(tmp_path, agency_id)
    recorded = _RECORDED.read_bytes()
    days = [d - _DAY * n for n in range(5, -1, -1)]
    polls = [_utc(day, 6) for day in days]
    for at in polls:
        poller.write_poll(rt, at, _restamped(recorded, at))
    poller.pack_closed_days(rt, d + _DAY)
    # Ingested through the first day; the four after it waited out the outage.
    poller.mark_ingested_through(rt, f"{days[0]:%Y%m%d}")

    assert poller.run_daily_step(tmp_path, {agency_id: {"ingest"}}) == {}

    for day, at in zip(days[1:], polls[1:], strict=True):
        assert _file_names(ch_client, agency_id, day, live=False) == {member_name(at)}, day
    assert poller.ingested_through(rt) == f"{d:%Y%m%d}"


def _archive(rt: Path, recorded: bytes, *instants: datetime) -> None:
    for at in instants:
        poller.write_poll(rt, at, _restamped(recorded, at))
    poller.pack_closed_days(rt, max(at.date() for at in instants) + _DAY)


def test_an_insert_failure_holds_the_marker_before_its_tarball(pg_conn, ch_client, agency_id, tmp_path):
    d = jst_today() - _DAY
    rt = poller.rt_dir(tmp_path, agency_id)
    recorded = _RECORDED.read_bytes()
    _archive(rt, recorded, _utc(d - _DAY, 6))
    assert poller.run_daily_step(tmp_path, {agency_id: {"ingest"}}) == {}
    assert poller.ingested_through(rt) == f"{d - _DAY:%Y%m%d}"

    _archive(rt, recorded, _utc(d, 6))
    with patch("pipeline.ingest.insert_updates", side_effect=ConnectionResetError("clickhouse went away")):
        assert poller.run_daily_step(tmp_path, {agency_id: {"ingest"}}) == {agency_id: {"ingest"}}
    assert poller.ingested_through(rt) == f"{d - _DAY:%Y%m%d}"
    assert not _file_names(ch_client, agency_id, d, live=False)

    assert poller.run_daily_step(tmp_path, {agency_id: {"ingest"}}) == {}
    assert poller.ingested_through(rt) == f"{d:%Y%m%d}"
    assert _file_names(ch_client, agency_id, d, live=False) == {member_name(_utc(d, 6))}


def test_a_parse_failure_does_not_hold_the_marker(pg_conn, ch_client, agency_id, tmp_path):
    from pipeline.strategies import aomori_regex

    d = jst_today() - _DAY
    rt = poller.rt_dir(tmp_path, agency_id)
    good, bad = _utc(d, 6), _utc(d, 7)
    _archive(rt, _RECORDED.read_bytes(), good, bad)
    real_parse = aomori_regex.parse_feed

    def parse(raw, ts, file_name, aid, conn):
        if file_name == member_name(bad):
            raise ValueError("truncated varint")
        return real_parse(raw, ts, file_name, aid, conn)

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=parse):
        assert poller.run_daily_step(tmp_path, {agency_id: {"ingest"}}) == {}
    assert poller.ingested_through(rt) == f"{d:%Y%m%d}"
    assert _file_names(ch_client, agency_id, d, live=False) == {member_name(good)}
