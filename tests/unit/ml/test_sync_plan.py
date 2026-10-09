from datetime import date

from ml.sync import Archive, IngestDays, LoadStatic, is_final, load_done, parse_keys, plan, save_done


def _rt(agency: int, day: str) -> Archive:
    return Archive(agency, date.fromisoformat(day), f"rt/{agency}/{day.replace('-', '')}.tar.gz", "rt")


def _static(agency: int, day: str) -> Archive:
    return Archive(agency, date.fromisoformat(day), f"static/{agency}/gtfs_static_{day.replace('-', '')}.zip", "static")


def test_listing_keeps_archives_and_drops_everything_else():
    keys = [
        "rt/8/20260606.tar.gz",
        "static/8/gtfs_static_20260601.zip",
        "rt/8/notes.txt",
        "backups/transit-2026-06-01.sql.gz",
    ]
    assert parse_keys(keys) == [_rt(8, "2026-06-06"), _static(8, "2026-06-01")]


def test_each_static_version_loads_before_the_days_it_governs():
    archives = [_rt(8, "2026-06-06"), _rt(8, "2026-06-08"), _static(8, "2026-06-01"), _static(8, "2026-06-07")]
    assert plan(archives, done=set()) == [
        LoadStatic(_static(8, "2026-06-01")),
        IngestDays(8, (_rt(8, "2026-06-06"),)),
        LoadStatic(_static(8, "2026-06-07")),
        IngestDays(8, (_rt(8, "2026-06-08"),)),
    ]


def test_a_static_version_published_on_a_day_loads_before_that_days_realtime():
    archives = [_rt(8, "2026-06-07"), _static(8, "2026-06-07")]
    assert plan(archives, done=set())[0] == LoadStatic(_static(8, "2026-06-07"))


def test_archives_already_done_are_left_out_and_agencies_stay_apart():
    archives = [_rt(8, "2026-06-06"), _rt(8, "2026-06-07"), _rt(9, "2026-06-06")]
    done = {_rt(8, "2026-06-06").key}
    assert plan(archives, done) == [IngestDays(8, (_rt(8, "2026-06-07"),)), IngestDays(9, (_rt(9, "2026-06-06"),))]


def test_a_utc_day_archive_is_final_only_once_the_jst_day_after_it_has_ended():
    archive = _rt(8, "2026-10-07")
    assert not is_final(archive, today_jst=date(2026, 10, 8))
    assert is_final(archive, today_jst=date(2026, 10, 9))
    assert is_final(_static(8, "2026-10-08"), today_jst=date(2026, 10, 8))


def test_the_done_set_survives_a_round_trip_and_starts_empty(tmp_path):
    path = tmp_path / "state" / "sync.json"
    assert load_done(path) == set()
    save_done(path, {"rt/8/20260606.tar.gz"})
    assert load_done(path) == {"rt/8/20260606.tar.gz"}
