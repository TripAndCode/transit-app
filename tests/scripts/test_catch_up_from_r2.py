"""`scripts/catch_up_from_r2.py` against throwaway Postgres and ClickHouse, with a
local directory standing in for the R2 bucket.

Every run catches one static_join agency up across one timetable load: rows
through JST D-2 are in, R2 holds archives D-2..D and a timetable collected on D.
Archive D-1 runs from 09:00 JST on D-1 to 09:00 JST on D, so it holds a member
of each timetable's.
"""

from __future__ import annotations

import io
import pathlib
import tarfile
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline.clickhouse import insert_updates, jst_date
from pipeline.static_loader import load_static
from scripts import catch_up_from_r2 as cu
from tests.fixtures.gtfs_rt import header_only_feed, stop_time_update, trip_update, trip_update_feed

DAY = timedelta(days=1)


class DirectoryStore:
    """R2's `rt/<id>/` and `static/<id>/` layout under a local directory."""

    description = "test directory"

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.downloads: list[str] = []

    def list(self, prefix: str, *, start_after: str | None = None) -> list[tuple[str, int]]:
        base = self.root / prefix
        if not base.is_dir():
            return []
        keys = sorted(f"{prefix}{p.name}" for p in base.iterdir())
        return [(k, (self.root / k).stat().st_size) for k in keys if start_after is None or k > start_after]

    def download(self, key: str, dest: pathlib.Path) -> None:
        self.downloads.append(key)
        dest.write_bytes((self.root / key).read_bytes())


def _timetable(path: pathlib.Path, departure: str, start: date) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nS1,Stop 1,34.39,132.46\n")
        zf.writestr("routes.txt", "route_id,route_short_name,route_long_name\nR1,1,Route 1\n")
        zf.writestr("trips.txt", "trip_id,route_id,trip_headsign,shape_id,service_id\nT1,R1,,,daily\n")
        zf.writestr(
            "stop_times.txt",
            f"trip_id,stop_sequence,stop_id,arrival_time,departure_time\nT1,1,S1,{departure},{departure}\n",
        )
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            f"daily,1,1,1,1,1,1,1,{start:%Y%m%d},{start + 180 * DAY:%Y%m%d}\n",
        )
    return path


def _member_name(instant: datetime) -> str:
    """The collector's name for a poll: its UTC day directory and UTC time."""
    return f"{instant:%Y%m%d}/TripUpdate_{instant:%H%M%S}.pb"


def _archive(path: pathlib.Path, polls: list[datetime]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        for instant in polls:
            raw = header_only_feed(int(instant.timestamp())) + trip_update_feed(
                trip_update("T1", stop_time_update(1, departure_delay=30), route_id="R1")
            )
            info = tarfile.TarInfo(_member_name(instant))
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))


def _utc(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


@pytest.fixture
def world(pg_conn, ch_client, tmp_path):
    """The agency, its loaded timetable, its rows through JST D-2, and R2.

    The clock is read once: every run gets the same ``now``, so a run that
    crosses JST midnight cannot move "today" away from the days seeded here.
    """
    now = datetime.now(timezone.utc)
    d = jst_date(now) - 3 * DAY
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES (%s, %s, %s) RETURNING agency_id",
            ("catch-up test", "http://example.com/tu.pb", "static_join"),
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    old = f"gtfs_static_{d - 10 * DAY:%Y%m%d}"
    load_static(str(_timetable(tmp_path / "loaded" / f"{old}.zip", "07:00:00", d - 10 * DAY)), agency_id, pg_conn)

    ingested = _utc(d - 2 * DAY, 3)  # JST D-2 12:00
    insert_updates(
        ch_client,
        agency_id,
        [(_member_name(ingested), ingested.isoformat(), "T1", "daily", "07:00:00", "R1", 1, 30)],
    )

    bucket = tmp_path / "bucket"
    rt = bucket / "rt" / str(agency_id)
    _archive(rt / f"{d - 2 * DAY:%Y%m%d}.tar.gz", [ingested])
    head, tail = _utc(d - DAY, 10), _utc(d - DAY, 16)  # JST D-1 19:00 and JST D 01:00
    _archive(rt / f"{d - DAY:%Y%m%d}.tar.gz", [head, tail])
    later = _utc(d, 10)  # JST D 19:00
    _archive(rt / f"{d:%Y%m%d}.tar.gz", [later])
    static = bucket / "static" / str(agency_id)
    _timetable(static / f"gtfs_static_{d - 20 * DAY:%Y%m%d}.zip", "06:00:00", d - 20 * DAY)
    new = f"gtfs_static_{d:%Y%m%d}"
    _timetable(static / f"{new}.zip", "08:00:00", d)

    return {
        "now": now,
        "agency_id": agency_id,
        "store": DirectoryStore(bucket),
        "dirs": ["--archives-dir", str(tmp_path / "raw_archives"), "--static-dir", str(tmp_path / "raw_static")],
        "old": old,
        "new": new,
        "ingested": _member_name(ingested),
        "head": _member_name(head),
        "tail": _member_name(tail),
        "later": _member_name(later),
    }


def _rows(ch_client, agency_id: int) -> dict[str, list[tuple]]:
    found: dict[str, list[tuple]] = {}
    for file_name, version, sec in ch_client.query(
        "SELECT file_name, static_version_id, scheduled_sec FROM updates WHERE agency_id = {a:UInt16}",
        parameters={"a": agency_id},
    ).result_rows:
        found.setdefault(file_name, []).append((version, sec))
    return found


def _runs(pg_conn, agency_id: int) -> dict[str, int]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT kind, count(*) FROM pipeline_runs WHERE agency_id = %s AND status = 'ok' GROUP BY kind",
            (agency_id,),
        )
        counts = dict(cur.fetchall())
    pg_conn.commit()
    return counts


def test_plan_mode_downloads_and_writes_nothing(world, pg_conn, ch_client, capsys):
    assert cu.main(world["dirs"], store=world["store"], now=world["now"]) == 0

    out = capsys.readouterr().out
    assert f"load   {world['new']}.zip" in out
    assert world["store"].downloads == []
    assert list(_rows(ch_client, world["agency_id"])) == [world["ingested"]]
    assert _runs(pg_conn, world["agency_id"]) == {}


def test_apply_joins_each_jst_day_to_the_timetable_in_force_on_it(world, pg_conn, ch_client):
    agency_id = world["agency_id"]

    assert cu.main([*world["dirs"], "--apply", "--yes"], store=world["store"], now=world["now"]) == 0

    rows = _rows(ch_client, agency_id)
    assert rows == {
        world["ingested"]: [(None, None)],
        # JST D-1 joins the timetable loaded before the run; the 07:00 departure.
        world["head"]: [(world["old"], 7 * 3600)],
        # Archive D-1's member past JST midnight is JST day D, after the load.
        world["tail"]: [(world["new"], 8 * 3600)],
        world["later"]: [(world["new"], 8 * 3600)],
    }
    with pg_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT static_version_id FROM static_trips WHERE agency_id = %s", (agency_id,))
        assert cur.fetchall() == [(world["new"],)]
    pg_conn.commit()
    assert _runs(pg_conn, agency_id) == {"ingest": 2, "static": 1}
    # The already-ingested archive is downloaded to finish its tail; the older
    # timetable in R2 is never fetched.
    assert sorted(world["store"].downloads) == sorted(
        [
            f"rt/{agency_id}/{world['ingested'][:8]}.tar.gz",
            f"rt/{agency_id}/{world['head'][:8]}.tar.gz",
            f"rt/{agency_id}/{world['later'][:8]}.tar.gz",
            f"static/{agency_id}/{world['new']}.zip",
        ]
    )


def test_a_rerun_writes_nothing_twice_and_reloads_nothing(world, pg_conn, ch_client):
    agency_id = world["agency_id"]
    assert cu.main([*world["dirs"], "--apply", "--yes"], store=world["store"], now=world["now"]) == 0
    first = _rows(ch_client, agency_id)
    runs = _runs(pg_conn, agency_id)

    assert cu.main([*world["dirs"], "--apply", "--yes"], store=world["store"], now=world["now"]) == 0

    assert _rows(ch_client, agency_id) == first
    assert _runs(pg_conn, agency_id)["static"] == runs["static"]


def test_apply_with_analyze_builds_the_aggregates(world, pg_conn):
    assert cu.main([*world["dirs"], "--apply", "--yes", "--analyze"], store=world["store"], now=world["now"]) == 0

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agg_meta WHERE agency_id = %s", (world["agency_id"],))
        assert cur.fetchone()[0] == 1
    pg_conn.commit()
    assert _runs(pg_conn, world["agency_id"])["analyze"] == 1


def test_apply_refuses_an_agency_with_no_timetable_and_writes_nothing(world, pg_conn, ch_client, capsys):
    agency_id = world["agency_id"]
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM static_stops WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()

    assert cu.main([*world["dirs"], "--apply", "--yes"], store=world["store"], now=world["now"]) == 1

    assert "REQUIRES_STATIC_TIMETABLE" in capsys.readouterr().out
    assert list(_rows(ch_client, agency_id)) == [world["ingested"]]
    assert world["store"].downloads == []


def _empty_archive(path: pathlib.Path) -> None:
    """An archive of a day the collector polled nothing from: its day directory alone."""
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(path.name.split(".")[0])
        info.type = tarfile.DIRTYPE
        tf.addfile(info)


def test_a_timetable_is_not_loaded_after_days_that_wrote_no_rows(world, pg_conn, ch_client, capsys):
    """Archive D-1 is empty, so after the days before D are ingested `updates`
    still ends on JST D-2. Loading D then would leave it ahead of the next
    run's open day, which refuses the agency, so the run stops before it."""
    agency_id = world["agency_id"]
    _empty_archive(world["store"].root / "rt" / str(agency_id) / f"{world['head'][:8]}.tar.gz")

    assert cu.main([*world["dirs"], "--apply", "--yes"], store=world["store"], now=world["now"]) == 1

    assert f"{world['new']}.zip is not loaded" in capsys.readouterr().err
    with pg_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT static_version_id FROM static_trips WHERE agency_id = %s", (agency_id,))
        assert cur.fetchall() == [(world["old"],)]
    pg_conn.commit()
    assert _runs(pg_conn, agency_id) == {"ingest": 1}
    assert f"static/{agency_id}/{world['new']}.zip" not in world["store"].downloads
    assert list(_rows(ch_client, agency_id)) == [world["ingested"]]


def test_allow_gaps_loads_a_timetable_after_days_taken_as_empty(world, pg_conn, ch_client, capsys):
    agency_id = world["agency_id"]
    _empty_archive(world["store"].root / "rt" / str(agency_id) / f"{world['head'][:8]}.tar.gz")

    argv = [*world["dirs"], "--apply", "--yes", "--allow-gaps"]
    assert cu.main(argv, store=world["store"], now=world["now"]) == 0

    assert _rows(ch_client, agency_id) == {
        world["ingested"]: [(None, None)],
        world["later"]: [(world["new"], 8 * 3600)],
    }
    assert _runs(pg_conn, agency_id) == {"ingest": 2, "static": 1}

    # Archive D held rows, so the open day passed D and the next plan goes on.
    capsys.readouterr()
    assert cu.main(world["dirs"], store=world["store"], now=world["now"]) == 0
    assert "REFUSED" not in capsys.readouterr().out


def test_an_escaped_load_with_no_rows_after_it_leaves_the_next_plan_refused(world, capsys):
    """With D's own archive empty too, nothing moves the open day past D-1, so
    the timetable stays ahead of it: the next plan is refused, --allow-gaps or
    not, and its refusal names the way back."""
    agency_id = world["agency_id"]
    rt = world["store"].root / "rt" / str(agency_id)
    _empty_archive(rt / f"{world['head'][:8]}.tar.gz")
    _empty_archive(rt / f"{world['later'][:8]}.tar.gz")
    argv = [*world["dirs"], "--apply", "--yes", "--allow-gaps"]
    assert cu.main(argv, store=world["store"], now=world["now"]) == 0
    capsys.readouterr()

    assert cu.main([*world["dirs"], "--allow-gaps"], store=world["store"], now=world["now"]) == 0

    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "load_static" in out and "--allow-gaps" in out
