import subprocess
from datetime import date
from pathlib import Path

from ml.sync import Archive, IngestDays, LoadStatic, execute, list_keys, load_done


def _rt(agency: int, day: str) -> Archive:
    return Archive(agency, date.fromisoformat(day), f"rt/{agency}/{day.replace('-', '')}.tar.gz", "rt")


def _static(agency: int, day: str) -> Archive:
    return Archive(agency, date.fromisoformat(day), f"static/{agency}/gtfs_static_{day.replace('-', '')}.zip", "static")


class Recorder:
    """Stands in for subprocess: records each command, writes the file an
    `aws s3 cp` would, and fails the commands `fail_when` picks."""

    def __init__(self, fail_when=lambda cmd: False):
        self.commands: list[list[str]] = []
        self.fail_when = fail_when

    def __call__(self, cmd):
        self.commands.append(list(cmd))
        if self.fail_when(cmd):
            raise subprocess.CalledProcessError(75, cmd)
        if list(cmd[:3]) == ["aws", "s3", "cp"]:
            Path(cmd[4]).write_bytes(b"archive")


def _execute(tmp_path, actions, run, today="2026-10-09"):
    done: set[str] = set()
    failures = execute(
        actions,
        bucket="transit-archives",
        endpoint="https://r2.example",
        work_dir=tmp_path / "work",
        state_path=tmp_path / "state.json",
        done=done,
        today_jst=date.fromisoformat(today),
        run=run,
        python="python",
    )
    return failures, load_done(tmp_path / "state.json")


def test_a_static_version_downloads_then_loads(tmp_path):
    run = Recorder()
    failures, done = _execute(tmp_path, [LoadStatic(_static(8, "2026-06-01"))], run)
    assert failures == []
    assert run.commands[0][:3] == ["aws", "s3", "cp"]
    assert run.commands[1][:3] == ["python", "gtfs_pipeline.py", "load_static"]
    assert run.commands[1][-2:] == ["--agency-id", "8"]
    assert done == {"static/8/gtfs_static_20260601.zip"}


def test_days_ingest_as_one_folder_and_only_final_days_are_done(tmp_path):
    run = Recorder()
    days = (_rt(8, "2026-10-06"), _rt(8, "2026-10-07"), _rt(8, "2026-10-08"))
    failures, done = _execute(tmp_path, [IngestDays(8, days)], run, today="2026-10-09")
    assert failures == []
    ingests = [c for c in run.commands if c[1:3] == ["gtfs_pipeline.py", "ingest"]]
    assert len(ingests) == 1
    assert done == {"rt/8/20261006.tar.gz", "rt/8/20261007.tar.gz"}


def test_a_failing_agency_stops_there_and_the_next_agency_still_runs(tmp_path):
    run = Recorder(fail_when=lambda cmd: cmd[1:3] == ["gtfs_pipeline.py", "ingest"] and cmd[-1] == "8")
    actions = [
        IngestDays(8, (_rt(8, "2026-10-01"),)),
        IngestDays(8, (_rt(8, "2026-10-02"),)),
        IngestDays(9, (_rt(9, "2026-10-01"),)),
    ]
    failures, done = _execute(tmp_path, actions, run)
    assert len(failures) == 1 and failures[0].startswith("agency 8:")
    assert done == {"rt/9/20261001.tar.gz"}
    assert not any("rt/8/20261002.tar.gz" in " ".join(c) for c in run.commands)


def test_downloads_are_removed_once_their_action_ends(tmp_path):
    run = Recorder()
    _execute(tmp_path, [IngestDays(8, (_rt(8, "2026-10-01"),))], run)
    downloaded = Path(next(c for c in run.commands if c[:3] == ["aws", "s3", "cp"])[4])
    assert downloaded.is_relative_to(tmp_path / "work")
    assert not downloaded.exists()


def test_the_listing_keeps_object_keys_only():
    listings = {
        "s3://transit-archives/rt/": "2026-10-09 09:10:01     123456 rt/8/20261008.tar.gz\n",
        "s3://transit-archives/static/": "2026-10-09 09:10:02         42 static/8/gtfs_static_20261008.zip\n",
    }
    keys = list_keys("transit-archives", "https://r2.example", capture=lambda cmd: listings[cmd[3]])
    assert keys == ["rt/8/20261008.tar.gz", "static/8/gtfs_static_20261008.zip"]
