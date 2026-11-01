"""The R2 catch-up planner (`scripts/catch_up_from_r2.py`), as a pure function
over listings and state.

Archive `rt/<id>/D.tar.gz` holds what the collector polled on UTC day D: JST day
D from 09:00 and JST day D+1 until 09:00. Timetable `gtfs_static_D.zip` was
fetched at 09:00 JST on D and is in force from JST day D, so every plan below
splits archives at JST midnight, not at archive boundaries.
"""

from __future__ import annotations

import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts import catch_up_from_r2 as cu

TODAY = date(2026, 11, 1)


def _day(token: str) -> date:
    return datetime.strptime(token, "%Y%m%d").date()


def _days(first: str, last: str) -> list[str]:
    start, end = _day(first), _day(last)
    return [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range((end - start).days + 1)]


def _archives(agency_id: int, tokens: list[str], size: int = 1_000) -> list[cu.RemoteFile]:
    files, ignored = cu.archive_files([(f"rt/{agency_id}/{t}.tar.gz", size) for t in tokens])
    assert ignored == []
    return files


def _timetables(agency_id: int, tokens: list[str], size: int = 100) -> list[cu.RemoteFile]:
    files, ignored = cu.timetable_files([(f"static/{agency_id}/gtfs_static_{t}.zip", size) for t in tokens])
    assert ignored == []
    return files


def _state(
    agency_id: int = 8,
    *,
    loaded: str | None = "gtfs_static_20261014",
    newest_row: datetime | None = datetime(2026, 10, 23, 14, 59, 9, tzinfo=timezone.utc),
    newest_file: str | None = None,
    requires_timetable: bool = True,
    has_timetable: bool = True,
) -> cu.AgencyState:
    """``newest_file`` defaults to the collector's name for ``newest_row``: its
    UTC day directory and UTC time."""
    if newest_file is None and newest_row is not None:
        newest_file = f"{newest_row:%Y%m%d}/TripUpdate_{newest_row:%H%M%S}.pb"
    return cu.AgencyState(
        agency_id=agency_id,
        name=f"agency {agency_id}",
        requires_timetable=requires_timetable,
        has_timetable=has_timetable,
        loaded_version=loaded,
        newest_row=newest_row,
        newest_file=newest_file,
    )


def _shape(plan: cu.AgencyPlan) -> list[tuple]:
    """Each step as plain values: ("ingest", first JST day, cutoff, archive
    tokens) or ("load", timetable name)."""
    out: list[tuple] = []
    for step in plan.steps:
        if isinstance(step, cu.IngestStep):
            out.append(
                (
                    "ingest",
                    step.first_day.strftime("%Y%m%d"),
                    step.cutoff.strftime("%Y%m%d") if step.cutoff else None,
                    [a.day.strftime("%Y%m%d") for a in step.archives],
                )
            )
        else:
            out.append(("load", step.timetable.name))
    return out


def _written(steps: list, today: date = TODAY) -> list[tuple[date, date]]:
    """Every (archive day, JST day) pair the ingest steps write, in step order."""
    pairs = []
    for step in steps:
        if isinstance(step, cu.IngestStep):
            stop = step.cutoff or today
            for archive in step.archives:
                pairs += [
                    (archive.day, d)
                    for d in (archive.day, archive.day + timedelta(days=1))
                    if step.first_day <= d < stop
                ]
    return pairs


def test_interleaves_two_newer_timetables_at_jst_midnight():
    """Timetable 20261014 loaded, rows through JST 10-23, and R2 holding
    20261025 and 20261028. Archive 20261024's tail is JST 10-25, so it is read
    again after 20261025 loads; the same for 20261027."""
    plan = cu.plan_agency(
        _state(),
        _archives(8, _days("20261018", "20261031")),
        _timetables(8, ["20261001", "20261008", "20261025", "20261028"]),
        today=TODAY,
    )

    assert plan.refusal is None
    assert _shape(plan) == [
        ("ingest", "20261024", "20261025", ["20261023", "20261024"]),
        ("load", "gtfs_static_20261025.zip"),
        ("ingest", "20261025", "20261028", ["20261024", "20261025", "20261026", "20261027"]),
        ("load", "gtfs_static_20261028.zip"),
        ("ingest", "20261028", None, ["20261027", "20261028", "20261029", "20261030", "20261031"]),
    ]


def test_each_archive_day_pair_is_written_once_and_none_is_missed():
    plan = cu.plan_agency(
        _state(),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261025", "20261028"]),
        today=TODAY,
    )
    pairs = _written(plan.steps)

    assert len(pairs) == len(set(pairs))
    # JST 10-24 through 10-31 are the closed days still to write; each comes
    # from its own archive's head and the previous archive's tail.
    expected = {(d - timedelta(days=1), d) for d in map(_day, _days("20261024", "20261031"))}
    expected |= {(d, d) for d in map(_day, _days("20261024", "20261031"))}
    assert set(pairs) == expected


def test_no_newer_timetable_ingests_everything_in_one_step():
    """The loaded timetable is newer than any in R2: nothing to load."""
    plan = cu.plan_agency(
        _state(9),
        _archives(9, _days("20261023", "20261031")),
        _timetables(9, ["20260816", "20261001", "20261009"]),
        today=TODAY,
    )

    assert plan.refusal is None
    assert _shape(plan) == [("ingest", "20261024", None, _days("20261023", "20261031"))]
    assert plan.timetables == []


def test_archive_dated_like_a_timetable_is_ingested_after_it_loads():
    """Timetable D was fetched at the instant archive D began, so archive D
    belongs wholly to it; archive D-1 is split: its JST D-1 head before the
    load, its JST D tail after."""
    plan = cu.plan_agency(
        _state(),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261026"]),
        today=TODAY,
    )
    steps = _shape(plan)
    load_at = steps.index(("load", "gtfs_static_20261026.zip"))

    before, after = steps[:load_at], steps[load_at + 1 :]
    assert ["20261026" in step[3] for step in before] == [False]
    assert "20261026" in after[0][3]
    assert "20261025" in before[0][3] and "20261025" in after[0][3]
    assert after[0][1] == "20261026"
    assert (_day("20261025"), _day("20261025")) in _written(plan.steps[:load_at])
    assert (_day("20261025"), _day("20261026")) in _written(plan.steps[load_at + 1 :])


def test_timetables_on_consecutive_days_each_get_their_one_jst_day():
    plan = cu.plan_agency(
        _state(),
        _archives(8, _days("20261023", "20261028")),
        _timetables(8, ["20261025", "20261026"]),
        today=date(2026, 10, 29),
    )

    assert _shape(plan) == [
        ("ingest", "20261024", "20261025", ["20261023", "20261024"]),
        ("load", "gtfs_static_20261025.zip"),
        ("ingest", "20261025", "20261026", ["20261024", "20261025"]),
        ("load", "gtfs_static_20261026.zip"),
        ("ingest", "20261026", None, ["20261025", "20261026", "20261027", "20261028"]),
    ]


def test_a_day_missing_in_r2_stops_the_plan_before_it():
    """Going past a missing archive would make it permanently older than the
    newest ingested day, so the plan stops there unless told the day is lost.
    A timetable in force from the missing day on waits with it."""
    tokens = [t for t in _days("20261023", "20261031") if t != "20261025"]
    plan = cu.plan_agency(_state(), _archives(8, tokens), _timetables(8, ["20261025", "20261028"]), today=TODAY)

    assert plan.refusal is None
    assert plan.gaps == [_day("20261025")]
    assert plan.stopped_at == _day("20261025")
    assert _shape(plan) == [("ingest", "20261024", "20261025", ["20261023", "20261024"])]
    assert [t.name for t in plan.deferred] == ["gtfs_static_20261025.zip", "gtfs_static_20261028.zip"]


def test_a_missing_day_can_be_passed_when_the_operator_says_it_is_lost():
    tokens = [t for t in _days("20261023", "20261031") if t != "20261025"]
    plan = cu.plan_agency(_state(), _archives(8, tokens), _timetables(8, ["20261025"]), today=TODAY, allow_gaps=True)

    assert plan.gaps == [_day("20261025")]
    assert plan.stopped_at is None
    assert _shape(plan) == [
        ("ingest", "20261024", "20261025", ["20261023", "20261024"]),
        ("load", "gtfs_static_20261025.zip"),
        ("ingest", "20261025", None, ["20261024", *_days("20261026", "20261031")]),
    ]


def test_the_newest_day_not_yet_uploaded_is_not_a_gap():
    """Yesterday's archive reaches R2 only after 09:10 JST."""
    plan = cu.plan_agency(_state(), _archives(8, _days("20261023", "20261030")), [], today=TODAY)

    assert plan.gaps == []
    assert _shape(plan) == [("ingest", "20261024", None, _days("20261023", "20261030"))]


def test_agency_1_static_naming():
    """An agency whose rows take their schedule from the trip_id, whose
    loaded timetable is named by aomori_index_scrape (the fetching host's local
    date), and whose R2 timetables are all older, with names R2 never orders
    left alone."""
    old = [f"2026{m:02d}{d:02d}" for m, d in [(4, 23), (5, 1), (6, 6), (8, 17)]]
    listing = [(f"static/1/gtfs_static_{t}.zip", 1_360_000) for t in old]
    listing += [("static/1/latest.zip", 10), ("static/1/_manifest.json", 10)]
    timetables, ignored = cu.timetable_files(listing)
    assert ignored == ["latest.zip", "_manifest.json"]

    plan = cu.plan_agency(
        _state(
            1,
            loaded="gtfs_static_20261015",
            newest_row=datetime(2026, 10, 14, 2, 2, 52, tzinfo=timezone.utc),
            requires_timetable=False,
        ),
        _archives(1, _days("20261014", "20261031")),
        timetables,
        today=TODAY,
    )

    assert plan.refusal is None
    assert cu.version_day("gtfs_static_20261015") == date(2026, 10, 15)
    assert cu.version_day("hiroden_static") is None
    assert _shape(plan) == [("ingest", "20261015", None, _days("20261014", "20261031"))]


def test_an_agency_that_reads_no_timetable_is_not_refused_for_a_late_load():
    plan = cu.plan_agency(
        _state(
            1,
            loaded="gtfs_static_20261030",
            newest_row=datetime(2026, 10, 14, 2, 2, 52, tzinfo=timezone.utc),
            requires_timetable=False,
        ),
        _archives(1, _days("20261014", "20261031")),
        [],
        today=TODAY,
    )

    assert plan.refusal is None
    assert plan.notes


def test_refuses_an_agency_with_no_timetable_loaded():
    plan = cu.plan_agency(
        _state(has_timetable=False, loaded=None),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261008", "20261025"]),
        today=TODAY,
    )

    assert plan.steps == []
    assert plan.refusal is not None
    assert "REQUIRES_STATIC_TIMETABLE" in plan.refusal
    # The zip in force on the first JST day still to write, JST 10-24.
    assert "gtfs_static_20261008.zip" in plan.refusal


def test_refuses_a_timetable_loaded_ahead_of_the_days_still_to_write():
    """Rows through JST 10-23 and 20261026 already loaded: JST 10-24 and
    10-25 would join a timetable that was not yet in force."""
    plan = cu.plan_agency(
        _state(loaded="gtfs_static_20261026"),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261008", "20261026"]),
        today=TODAY,
    )

    assert plan.steps == []
    assert plan.refusal is not None
    assert "gtfs_static_20261008.zip" in plan.refusal


def test_a_timetable_in_force_from_the_first_day_to_write_is_accepted():
    """Rows through JST 10-23 and 20261024 loaded: JST 10-24 is the first day
    still to write, and 20261024 is in force from it."""
    plan = cu.plan_agency(
        _state(53, loaded="gtfs_static_20261024"),
        _archives(53, _days("20261023", "20261031")),
        _timetables(53, ["20261006", "20261024"]),
        today=TODAY,
    )

    assert plan.refusal is None
    assert _shape(plan) == [("ingest", "20261024", None, _days("20261023", "20261031"))]


def test_refuses_an_agency_with_no_rows():
    plan = cu.plan_agency(_state(newest_row=None), _archives(8, _days("20261023", "20261031")), [], today=TODAY)

    assert plan.steps == []
    assert plan.refusal is not None


def test_refuses_an_undated_loaded_timetable_when_r2_has_timetables():
    plan = cu.plan_agency(
        _state(loaded="hiroden_static"), _archives(8, ["20261023"]), _timetables(8, ["20261025"]), today=TODAY
    )

    assert plan.refusal is not None
    assert "hiroden_static" in plan.refusal


def test_refuses_a_day_listed_twice():
    one = cu.RemoteFile("rt/8/20261024.tar.gz", "20261024.tar.gz", date(2026, 10, 24), 1)
    plan = cu.plan_agency(_state(), [one, one], [], today=TODAY)

    assert plan.refusal is not None
    assert "2026-10-24" in plan.refusal


def test_only_days_from_the_newest_ingested_through_yesterday_are_planned():
    plan = cu.plan_agency(_state(), _archives(8, _days("20261001", "20261101")), [], today=TODAY)

    assert _shape(plan) == [("ingest", "20261024", None, _days("20261023", "20261031"))]


def test_timetables_in_force_only_for_days_already_written_are_skipped():
    plan = cu.plan_agency(
        _state(loaded="gtfs_static_20261001"),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261008", "20261014", "20261025"]),
        today=TODAY,
    )

    assert [t.name for t in plan.superseded] == ["gtfs_static_20261008.zip"]
    assert _shape(plan) == [
        ("load", "gtfs_static_20261014.zip"),
        ("ingest", "20261024", "20261025", ["20261023", "20261024"]),
        ("load", "gtfs_static_20261025.zip"),
        ("ingest", "20261025", None, _days("20261024", "20261031")),
    ]


def test_a_timetable_collected_today_loads_last_with_nothing_after_it():
    """It was fetched at 09:00 JST today, so it is in force from today, which
    has not closed: the plan ends on it, and the next run writes today."""
    plan = cu.plan_agency(
        _state(), _archives(8, _days("20261023", "20261031")), _timetables(8, ["20261101"]), today=TODAY
    )

    assert _shape(plan) == [
        ("ingest", "20261024", "20261101", _days("20261023", "20261031")),
        ("load", "gtfs_static_20261101.zip"),
    ]


def test_an_agency_already_caught_up_has_nothing_to_do():
    plan = cu.plan_agency(
        _state(newest_row=datetime(2026, 10, 31, 14, 59, tzinfo=timezone.utc)),
        _archives(8, _days("20261029", "20261031")),
        [],
        today=TODAY,
    )

    assert plan.refusal is None
    assert plan.steps == []


def test_sizes_count_each_file_once():
    plan = cu.plan_agency(
        _state(),
        _archives(8, _days("20261023", "20261031"), size=1_000),
        _timetables(8, ["20261025"], size=100),
        today=TODAY,
    )

    assert len(plan.archives) == 9
    assert plan.total_bytes == 9 * 1_000 + 100


def test_odd_archive_names_are_ignored_not_planned():
    files, ignored = cu.archive_files([("rt/8/20261024.tar.gz", 5), ("rt/8/20261024.tar.gz.part", 1), ("rt/8/x", 1)])

    assert [f.name for f in files] == ["20261024.tar.gz"]
    assert ignored == ["20261024.tar.gz.part", "x"]


def test_the_day_after_the_newest_row_is_open_when_that_row_ends_its_archive_head():
    """The usual state: the newest row is archive D's own JST D evening, so JST
    D is complete and D+1 is the first day to write."""
    state = _state(newest_row=datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc))  # JST 10-24 19:00

    assert state.open_day == date(2026, 10, 25)


def test_a_newest_row_from_the_previous_archives_tail_leaves_its_own_day_open():
    """JST 10-24 01:00 comes from archive 20261023's tail. Archive 20261024,
    which holds the rest of JST 10-24, has not been read, so 10-24 is still
    open and the timetable in force on it loads only after it is written."""
    tail = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
    plan = cu.plan_agency(
        _state(newest_row=tail),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261025"]),
        today=TODAY,
    )

    assert plan.state.open_day == date(2026, 10, 24)
    assert _shape(plan)[:2] == [
        ("ingest", "20261024", "20261025", ["20261023", "20261024"]),
        ("load", "gtfs_static_20261025.zip"),
    ]
    refused = cu.plan_agency(
        _state(newest_row=tail, loaded="gtfs_static_20261025"),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261014", "20261025"]),
        today=TODAY,
    )
    assert refused.refusal is not None


@pytest.mark.parametrize(
    ("open_day", "first_day", "refused"),
    [(date(2026, 10, 24), date(2026, 10, 24), False), (date(2026, 10, 25), date(2026, 10, 24), True)],
)
def test_an_ingest_step_refuses_to_start_before_the_open_day(open_day, first_day, refused):
    """Checked against `updates` again before every ingest step: a step that
    starts before the first day still open would write a closed day again or
    out of order."""
    if refused:
        with pytest.raises(cu.Refusal):
            cu.check_order(open_day, first_day)
    else:
        cu.check_order(open_day, first_day)


def test_a_timetable_past_the_last_archive_waits_for_its_days():
    """R2's archives end at 20261026, whose tail is JST 10-27. Timetable
    20261027 is in force from a day those archives still write; 20261028 is
    not, and loading it now would leave the next run's open day before it."""
    plan = cu.plan_agency(
        _state(),
        _archives(8, _days("20261023", "20261026")),
        _timetables(8, ["20261027", "20261028"]),
        today=TODAY,
    )

    assert plan.refusal is None
    assert _shape(plan) == [
        ("ingest", "20261024", "20261027", ["20261023", "20261024", "20261025", "20261026"]),
        ("load", "gtfs_static_20261027.zip"),
        ("ingest", "20261027", None, ["20261026"]),
    ]
    assert [t.name for t in plan.deferred] == ["gtfs_static_20261028.zip"]


def test_no_archive_in_r2_loads_no_timetable():
    plan = cu.plan_agency(_state(), [], _timetables(8, ["20261020", "20261028"]), today=TODAY)

    assert plan.refusal is None
    assert plan.steps == []
    assert [t.name for t in plan.deferred] == ["gtfs_static_20261020.zip", "gtfs_static_20261028.zip"]


def test_an_undated_loaded_timetable_is_only_noted_for_an_agency_that_reads_none():
    plan = cu.plan_agency(
        _state(
            1,
            loaded="aomori_static",
            newest_row=datetime(2026, 10, 23, 14, 59, tzinfo=timezone.utc),
            requires_timetable=False,
        ),
        _archives(1, _days("20261023", "20261031")),
        _timetables(1, ["20261025"]),
        today=TODAY,
    )

    assert plan.refusal is None
    assert plan.notes
    assert _shape(plan) == [("ingest", "20261024", None, _days("20261023", "20261031"))]


def test_an_open_day_that_already_holds_rows_is_noted_when_a_timetable_loads_on_it():
    """JST 10-24's first hours came from archive 20261023's tail under the
    loaded timetable; the rest of the day joins 20261024, so the day is split
    between the two."""
    plan = cu.plan_agency(
        _state(newest_row=datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)),
        _archives(8, _days("20261023", "20261031")),
        _timetables(8, ["20261024"]),
        today=TODAY,
    )

    assert _shape(plan)[0] == ("load", "gtfs_static_20261024.zip")
    assert any("2026-10-24" in note and "gtfs_static_20261014" in note for note in plan.notes)
    unsplit = cu.plan_agency(
        _state(), _archives(8, _days("20261023", "20261031")), _timetables(8, ["20261024"]), today=TODAY
    )
    assert unsplit.notes == []


class _Store:
    description = "stub"

    def __init__(self, payload: bytes):
        self.payload = payload
        self.downloads: list[str] = []

    def list(self, prefix, *, start_after=None):
        return []

    def download(self, key: str, dest: pathlib.Path) -> None:
        self.downloads.append(key)
        dest.write_bytes(self.payload)


def _remote(size: int) -> cu.RemoteFile:
    return cu.RemoteFile("rt/8/20261024.tar.gz", "20261024.tar.gz", date(2026, 10, 24), size)


def test_fetch_downloads_beside_the_file_and_moves_it_into_place(tmp_path):
    layout = cu.Layout(tmp_path / "rt", tmp_path / "static")
    store = _Store(b"x" * 10)

    path = layout.fetch(store, 8, _remote(10))

    assert path == tmp_path / "rt" / "8" / "20261024.tar.gz"
    assert path.read_bytes() == b"x" * 10
    assert sorted(p.name for p in path.parent.iterdir()) == ["20261024.tar.gz"]
    assert layout.fetch(store, 8, _remote(10)) == path
    assert store.downloads == ["rt/8/20261024.tar.gz"]


def test_a_short_download_raises_and_leaves_nothing_behind(tmp_path):
    layout = cu.Layout(tmp_path / "rt", tmp_path / "static")

    with pytest.raises(RuntimeError, match="10"):
        layout.fetch(_Store(b"x" * 4), 8, _remote(10))

    assert list((tmp_path / "rt" / "8").iterdir()) == []


def test_a_local_copy_of_another_size_is_refused_and_nothing_downloads(tmp_path):
    layout = cu.Layout(tmp_path / "rt", tmp_path / "static")
    local = tmp_path / "rt" / "8" / "20261024.tar.gz"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"old")
    store = _Store(b"x" * 10)

    with pytest.raises(cu.Refusal, match="move it aside"):
        layout.fetch(store, 8, _remote(10))

    assert store.downloads == []
    assert local.read_bytes() == b"old"
    assert layout.to_download(8, [_remote(10)])[1]


def test_a_timetable_loaded_ahead_of_the_open_day_is_refused_even_with_allow_gaps():
    """`updates` cannot tell days not yet ingested from days ingested with no
    rows, and ingesting the first kind against a timetable loaded ahead of them
    is the corruption this tool prevents, so no flag lets the plan go on. The
    refusal says how to recover either way."""
    state = _state(
        loaded="gtfs_static_20261031",
        newest_row=datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc),  # JST 10-29 19:00
    )
    archives = _archives(8, ["20261029", "20261030", "20261031"])
    timetables = _timetables(8, ["20261025", "20261031"])

    for allow_gaps in (False, True):
        plan = cu.plan_agency(state, archives, timetables, today=TODAY, allow_gaps=allow_gaps)
        assert plan.steps == []
        assert plan.refusal is not None
        assert "static/8/gtfs_static_20261025.zip" in plan.refusal
        assert "load_static" in plan.refusal
        assert "--allow-gaps" in plan.refusal
        assert "2026-10-31" in plan.refusal


def test_an_escaped_load_followed_by_rows_on_its_day_plans_on():
    """Once a day from the escaped timetable's on holds rows, the open day has
    caught up with it and the next run is not refused."""
    plan = cu.plan_agency(
        _state(
            loaded="gtfs_static_20261025",
            newest_row=datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc),  # JST 10-25 19:00
        ),
        _archives(8, _days("20261025", "20261031")),
        _timetables(8, ["20261025"]),
        today=TODAY,
    )

    assert plan.refusal is None
    assert _shape(plan) == [("ingest", "20261026", None, _days("20261025", "20261031"))]


def test_a_timetable_loads_once_updates_reaches_its_first_day():
    timetable = _timetables(8, ["20261025"])[0]

    assert cu.check_reached(date(2026, 10, 25), timetable, date(2026, 10, 24), allow_gaps=False) is None


def test_a_timetable_is_not_loaded_while_updates_stops_short_of_its_first_day():
    """The archives before it were ingested and wrote nothing past JST 10-23:
    loaded now, the timetable would sit ahead of the next run's open day."""
    timetable = _timetables(8, ["20261025"])[0]

    with pytest.raises(cu.Refusal) as refused:
        cu.check_reached(date(2026, 10, 24), timetable, date(2026, 10, 24), allow_gaps=False)
    message = str(refused.value)
    assert "gtfs_static_20261025.zip" in message
    assert "20261024" in message and "2026-10-23" in message
    assert "--allow-gaps" in message

    assert "no service" in message

    warning = cu.check_reached(date(2026, 10, 24), timetable, date(2026, 10, 24), allow_gaps=True)
    assert warning is not None and "2026-10-24" in warning
    # A load past empty days stays refused-free only once its own days hold rows.
    assert "next run" in warning
