"""scripts/rt_validate_sample.py: the daily GTFS-RT validator sample, with no
network, Docker or Java.

The fixtures under tests/fixtures/rt_validator are the official validator's own
results for agency 10's polls of 2026-11-10 at 09:04, 09:05 and 09:06 JST, each
occurrence list cut to its first three entries.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from datetime import date, datetime, timezone
from itertools import pairwise
from pathlib import Path

import pytest

from ml.sync import Archive
from scripts import rt_validate_sample as rv

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rt_validator"
DAY = date(2026, 11, 10)
UTC = timezone.utc


def _utc(hour: int, minute: int, second: int) -> datetime:
    return datetime(2026, 11, 10, hour, minute, second, tzinfo=UTC)


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return path


def _recorded() -> list[list[dict]]:
    return [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.results.json"))]


# ── the window ───────────────────────────────────────────────────────────────


def test_the_window_is_the_first_jst_hour_of_the_service_date():
    window = rv.sample_window(DAY)
    assert window.start == _utc(0, 0, 0)
    assert window.end == _utc(1, 0, 0)


@pytest.mark.parametrize(
    ("name", "instant"),
    [
        ("20261110/TripUpdate_004949.pb", _utc(0, 49, 49)),
        ("./20261110/TripUpdate_000004.pb", _utc(0, 0, 4)),
        ("20261110/VehiclePosition_004949.pb", None),
        ("20261110/TripUpdate_246000.pb", None),
        ("TripUpdate_004949.pb", None),
        ("20261110/TripUpdate_004949.pb.part", None),
        ("20261110", None),
    ],
)
def test_a_poll_is_timed_by_the_collectors_utc_name(name, instant):
    assert rv.poll_instant(name) == instant


def test_the_window_keeps_its_polls_in_time_order_and_excludes_its_end():
    names = [
        "20261110/TripUpdate_005959.pb",
        "20261110/TripUpdate_010000.pb",
        "20261110/TripUpdate_000000.pb",
        "20261110/TripUpdate_143000.pb",
        "20261110/VehiclePosition_000500.pb",
    ]
    assert rv.window_members(names, rv.sample_window(DAY)) == [
        ("20261110/TripUpdate_000000.pb", _utc(0, 0, 0)),
        ("20261110/TripUpdate_005959.pb", _utc(0, 59, 59)),
    ]


def test_a_poll_is_renamed_to_the_form_the_validator_reads_its_time_from():
    name = rv.validator_name(_utc(0, 49, 49))
    assert name == "TripUpdate-2026-11-10T00-49-49Z.pb"
    # -sort name takes the 20 characters before the extension as the instant.
    assert name.removesuffix(".pb")[-20:] == "2026-11-10T00-49-49Z"


def test_staging_writes_each_poll_in_the_window_under_its_validator_name(tmp_path):
    archive = _tar(
        tmp_path / "20261110.tar.gz",
        {
            "20261110/TripUpdate_000004.pb": b"first",
            "20261110/TripUpdate_000108.pb": b"second",
            "20261110/TripUpdate_010004.pb": b"after the window",
        },
    )
    dest = tmp_path / "rt"
    assert rv.stage_window(archive, dest, rv.sample_window(DAY)) == 2
    assert sorted(p.name for p in dest.iterdir()) == [
        "TripUpdate-2026-11-10T00-00-04Z.pb",
        "TripUpdate-2026-11-10T00-01-08Z.pb",
    ]
    assert (dest / "TripUpdate-2026-11-10T00-00-04Z.pb").read_bytes() == b"first"


# ── the timetable ────────────────────────────────────────────────────────────


def test_trimming_keeps_only_the_core_files_the_validator_reads(tmp_path):
    source = _zip(
        tmp_path / "gtfs_static_20261010.zip",
        {
            "agency.txt": "agency_id\n1\n",
            "stops.txt": "stop_id\nA\n",
            "translations.txt": "trans_id,lang,translation\n",
            "agency_jp.txt": "agency_id\n1\n",
            "nested/trips.txt": "trip_id\n",
        },
    )
    dest = tmp_path / "gtfs.zip"
    assert rv.trim_gtfs(source, dest) == ["agency.txt", "stops.txt"]
    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == ["agency.txt", "stops.txt"]
        assert zf.read("stops.txt") == b"stop_id\nA\n"


def _archives() -> list[Archive]:
    return [
        Archive(10, date(2026, 11, 9), "rt/10/20261109.tar.gz", "rt"),
        Archive(10, DAY, "rt/10/20261110.tar.gz", "rt"),
        Archive(8, DAY, "rt/8/20261110.tar.gz", "rt"),
        Archive(10, date(2026, 10, 1), "static/10/gtfs_static_20261001.zip", "static"),
        Archive(10, date(2026, 10, 10), "static/10/gtfs_static_20261010.zip", "static"),
        Archive(10, date(2026, 11, 11), "static/10/gtfs_static_20261111.zip", "static"),
        Archive(8, date(2026, 11, 10), "static/8/gtfs_static_20261110.zip", "static"),
    ]


def test_the_archive_is_the_one_named_for_the_service_date():
    assert rv.archive_for(_archives(), 10, DAY).key == "rt/10/20261110.tar.gz"
    assert rv.archive_for(_archives(), 10, date(2026, 11, 11)) is None


def test_the_timetable_is_the_newest_dated_on_or_before_the_service_date():
    assert rv.timetable_for(_archives(), 10, DAY).key == "static/10/gtfs_static_20261010.zip"
    assert rv.timetable_for(_archives(), 8, DAY).key == "static/8/gtfs_static_20261110.zip"
    assert rv.timetable_for(_archives(), 10, date(2026, 9, 30)) is None
    assert rv.timetable_for(_archives(), 9, DAY) is None


# ── the counts ───────────────────────────────────────────────────────────────


def test_the_recorded_results_count_per_rule():
    assert rv.rule_counts(_recorded()) == [
        {
            "rule_id": "E022",
            "severity": "ERROR",
            "title": "Sequential stop_time_update times are not increasing",
            "occurrences": 9,
            "files_with_rule": 3,
        },
        {
            "rule_id": "E039",
            "severity": "ERROR",
            "title": "FULL_DATASET feeds should not include entity.is_deleted",
            "occurrences": 9,
            "files_with_rule": 3,
        },
        {
            "rule_id": "W001",
            "severity": "WARNING",
            "title": "timestamp not populated",
            "occurrences": 9,
            "files_with_rule": 3,
        },
        {
            "rule_id": "W002",
            "severity": "WARNING",
            "title": "vehicle_id not populated",
            "occurrences": 9,
            "files_with_rule": 3,
        },
        {
            "rule_id": "W007",
            "severity": "WARNING",
            "title": "Refresh interval is more than 35 seconds",
            "occurrences": 2,
            "files_with_rule": 2,
        },
    ]


def _item(rule_id: str, occurrences: int) -> dict:
    return {
        "errorMessage": {"validationRule": {"errorId": rule_id, "severity": "WARNING", "title": rule_id}},
        "occurrenceList": [{"prefix": str(n)} for n in range(occurrences)],
    }


def test_a_rule_counts_each_file_once_and_an_empty_list_is_not_a_firing():
    counts = rv.rule_counts([[_item("W001", 2), _item("W001", 1), _item("W009", 0)], [_item("W001", 0)]])
    assert counts == [
        {"rule_id": "W001", "severity": "WARNING", "title": "W001", "occurrences": 3, "files_with_rule": 1}
    ]


# ── the summary ──────────────────────────────────────────────────────────────


def test_an_ok_summary_carries_every_field_of_the_schema():
    doc = rv.summary(
        agency_id=10,
        service_date=DAY,
        status="ok",
        files_given=4,
        files_validated=3,
        zip_key="static/10/gtfs_static_20261010.zip",
        validator=rv.validator_identity("docker"),
        runtime_sec=5.6,
        rules=rv.rule_counts(_recorded()),
    )
    assert doc == {
        "schema": 1,
        "agency_id": 10,
        "service_date": "2026-11-10",
        "window_start_utc": "2026-11-10T00:00:00Z",
        "window_end_utc": "2026-11-10T01:00:00Z",
        "files_given": 4,
        "files_validated": 3,
        "zip_key": "static/10/gtfs_static_20261010.zip",
        "validator": {
            "image": rv.IMAGE,
            "revision": rv.REVISION,
            "jar_sha256": rv.JAR_SHA256,
            "runner": "docker",
            "heap_max_mb": 512,
        },
        "runtime_sec": 5.6,
        "status": "ok",
        "reason": None,
        "rules": rv.rule_counts(_recorded()),
    }
    json.dumps(doc)


def test_the_image_is_pinned_by_digest():
    assert rv.IMAGE.startswith("ghcr.io/mobilitydata/gtfs-realtime-validator@sha256:")
    assert len(rv.IMAGE.split("@sha256:")[1]) == 64
    assert len(rv.JAR_SHA256) == 64


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "ok", "reason": "a reason"},
        {"status": "no_archive"},
        {"status": "validator_failed", "reason": "boom", "rules": [{"rule_id": "W001"}]},
        {"status": "unknown", "reason": "x"},
    ],
)
def test_a_summary_states_a_reason_exactly_when_it_is_not_ok(kwargs):
    with pytest.raises(ValueError):
        rv.summary(agency_id=10, service_date=DAY, validator=rv.validator_identity("jar"), **kwargs)


def test_the_summary_is_published_under_the_agency_and_date():
    assert rv.summary_key(10, DAY) == "validation/10/20261110.json"


# ── one agency ───────────────────────────────────────────────────────────────

_POLLS = {
    "20261110/TripUpdate_000409.pb": b"a",
    "20261110/TripUpdate_000509.pb": b"b",
    "20261110/TripUpdate_000550.pb": b"b",
    "20261110/TripUpdate_000610.pb": b"c",
}
_GTFS = {"agency.txt": "agency_id\n1\n", "translations.txt": "trans_id,lang,translation\n"}


def _fetch(tmp_path: Path, polls: dict[str, bytes] = _POLLS):
    fetched = []

    def fetch(key: str, dest: Path) -> None:
        fetched.append(key)
        if key.startswith("rt/"):
            _tar(tmp_path / "source.tar.gz", polls)
            shutil.copy(tmp_path / "source.tar.gz", dest)
        else:
            _zip(dest, _GTFS)

    return fetch, fetched


def _deduplicating_validator(work: Path, label: str) -> None:
    """What the validator writes for _POLLS: no results for the poll
    byte-identical to the one before it, the recorded results for the rest."""
    with zipfile.ZipFile(work / "gtfs.zip") as zf:
        assert zf.namelist() == ["agency.txt"]
    staged = sorted((work / "rt").glob("*.pb"))
    assert len(staged) == 4
    for pb, recorded in zip([staged[0], staged[1], staged[3]], sorted(FIXTURES.glob("*.results.json")), strict=True):
        shutil.copy(recorded, pb.with_name(pb.name + ".results.json"))


def test_a_poll_the_validator_skips_as_a_duplicate_is_given_but_not_validated(tmp_path):
    fetch, fetched = _fetch(tmp_path)
    doc = rv.validate_agency(
        10, DAY, _archives(), fetch=fetch, run_validator=_deduplicating_validator, runner="jar", work_root=tmp_path
    )
    assert fetched == ["rt/10/20261110.tar.gz", "static/10/gtfs_static_20261010.zip"]
    assert doc["status"] == "ok" and doc["reason"] is None
    assert (doc["files_given"], doc["files_validated"]) == (4, 3)
    assert doc["zip_key"] == "static/10/gtfs_static_20261010.zip"
    assert doc["rules"] == rv.rule_counts(_recorded())
    assert isinstance(doc["runtime_sec"], float)
    assert doc["validator"]["runner"] == "jar"


def test_the_raw_results_are_deleted_with_the_work_directory(tmp_path):
    fetch, _ = _fetch(tmp_path)
    rv.validate_agency(
        10, DAY, _archives(), fetch=fetch, run_validator=_deduplicating_validator, runner="jar", work_root=tmp_path
    )
    assert not list(tmp_path.rglob("*.results.json"))


def _never(work: Path, label: str) -> None:
    raise AssertionError("the validator must not run")


def test_no_archive_for_the_date_is_stated(tmp_path):
    fetch, fetched = _fetch(tmp_path)
    doc = rv.validate_agency(
        10, date(2026, 11, 11), _archives(), fetch=fetch, run_validator=_never, runner="jar", work_root=tmp_path
    )
    assert doc["status"] == "no_archive" and "rt/10/20261111.tar.gz" in doc["reason"]
    assert fetched == [] and doc["rules"] == [] and doc["files_given"] == 0


def test_an_archive_with_no_poll_in_the_window_is_stated_as_no_archive(tmp_path):
    fetch, fetched = _fetch(tmp_path, {"20261110/TripUpdate_143000.pb": b"a"})
    doc = rv.validate_agency(10, DAY, _archives(), fetch=fetch, run_validator=_never, runner="jar", work_root=tmp_path)
    assert doc["status"] == "no_archive" and "no TripUpdate poll" in doc["reason"]
    assert fetched == ["rt/10/20261110.tar.gz"] and doc["zip_key"] is None


def test_no_timetable_on_or_before_the_date_is_stated(tmp_path):
    archives = [a for a in _archives() if a.kind == "rt"]
    fetch, fetched = _fetch(tmp_path)
    doc = rv.validate_agency(10, DAY, archives, fetch=fetch, run_validator=_never, runner="jar", work_root=tmp_path)
    assert doc["status"] == "no_timetable" and doc["zip_key"] is None
    assert fetched == []


def test_a_validator_failure_is_recorded_not_raised(tmp_path):
    def failing(work: Path, label: str) -> None:
        raise rv.ValidatorFailed("the validator exited 1: CsvEntityIOException")

    fetch, _ = _fetch(tmp_path)
    doc = rv.validate_agency(10, DAY, _archives(), fetch=fetch, run_validator=failing, runner="jar", work_root=tmp_path)
    assert doc["status"] == "validator_failed"
    assert doc["reason"] == "the validator exited 1: CsvEntityIOException"
    assert doc["rules"] == [] and doc["files_given"] == 4 and isinstance(doc["runtime_sec"], float)


def test_a_failed_download_names_the_key_but_not_the_command(tmp_path):
    def fetch(key: str, dest: Path) -> None:
        raise subprocess.CalledProcessError(1, ["aws", "s3", "cp", f"s3://bucket/{key}", "--endpoint-url", "https://x"])

    doc = rv.validate_agency(10, DAY, _archives(), fetch=fetch, run_validator=_never, runner="jar", work_root=tmp_path)
    assert doc["status"] == "validator_failed"
    assert "rt/10/20261110.tar.gz" in doc["reason"] and "exit 1" in doc["reason"]
    assert "endpoint" not in doc["reason"] and "https://" not in doc["reason"]


_MALFORMED = [{"errorMessage": {"validationRule": {"errorId": "W001"}}, "occurrenceList": None}]


def _writing(document) -> Callable[[Path, str], None]:
    """A validator writing *document* as every staged poll's results."""

    def validator(work: Path, label: str) -> None:
        for pb in (work / "rt").glob("*.pb"):
            pb.with_name(pb.name + ".results.json").write_text(json.dumps(document))

    return validator


def test_a_results_document_of_an_unexpected_shape_is_a_failure_not_a_crash(tmp_path):
    fetch, _ = _fetch(tmp_path)
    doc = rv.validate_agency(
        10, DAY, _archives(), fetch=fetch, run_validator=_writing(_MALFORMED), runner="jar", work_root=tmp_path
    )
    assert doc["status"] == "validator_failed"
    assert doc["reason"].startswith("reading the validator's results failed")
    assert doc["rules"] == [] and doc["files_given"] == 4


def test_a_run_that_validated_no_poll_is_a_failure_not_a_clean_feed(tmp_path):
    def silent(work: Path, label: str) -> None:
        pass

    fetch, _ = _fetch(tmp_path)
    doc = rv.validate_agency(10, DAY, _archives(), fetch=fetch, run_validator=silent, runner="jar", work_root=tmp_path)
    assert doc["status"] == "validator_failed"
    assert doc["reason"] == "the validator wrote no results for 4 polls"
    assert doc["files_given"] == 4 and doc["files_validated"] == 0


@pytest.mark.parametrize(
    ("stderr", "line"),
    [
        (
            '[main] INFO Reading GTFS\nException in thread "main" org.x.CsvEntityIOException: io error\n'
            "\tat org.x.Reader.run(Reader.java:1)\nCaused by: org.x.MissingRequiredFieldException: table_name\n",
            'Exception in thread "main" org.x.CsvEntityIOException: io error',
        ),
        ("[main] INFO a\n[main] INFO last line\n\n", "[main] INFO last line"),
        ("", "no output"),
    ],
)
def test_a_failure_is_described_by_its_exception_line(stderr, line):
    assert rv.failure_line(stderr) == line


# ── the validator command ────────────────────────────────────────────────────


def test_the_container_runs_the_pinned_image_isolated_and_capped(tmp_path):
    cmd = rv.docker_command(tmp_path, "rt-validate-10-20261110")
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert ("--name", "rt-validate-10-20261110") in pairwise(cmd)
    assert ("--network", "none") in pairwise(cmd)
    assert ("--memory", rv.CONTAINER_MEMORY) in pairwise(cmd)
    assert ("--cpus", "2") in pairwise(cmd)
    assert ("-v", f"{tmp_path}:/data") in pairwise(cmd)
    assert not {"-e", "--env", "--env-file"} & set(cmd)
    assert rv.IMAGE in cmd
    tail = cmd[cmd.index(rv.IMAGE) + 1 :]
    assert tail == [
        "-Xmx512m",
        "-cp",
        rv.IMAGE_JAR,
        rv.MAIN_CLASS,
        "-gtfs",
        "/data/gtfs.zip",
        "-gtfsRealtimePath",
        "/data/rt",
        "-sort",
        "name",
    ]


def test_a_local_jar_runs_the_same_entry_point(tmp_path):
    jar = tmp_path / "validator.jar"
    assert rv.jar_command(jar, tmp_path) == [
        "java",
        "-Xmx512m",
        "-cp",
        str(jar),
        rv.MAIN_CLASS,
        "-gtfs",
        str(tmp_path / "gtfs.zip"),
        "-gtfsRealtimePath",
        str(tmp_path / "rt"),
        "-sort",
        "name",
    ]


def test_a_container_that_runs_past_the_timeout_is_removed(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, rv.TIMEOUT_SEC)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(rv.subprocess, "run", fake_run)
    with pytest.raises(rv.ValidatorFailed, match="ran past"):
        rv.docker_validator(tmp_path, "10-20261110")
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "rm", "-f", name]


def test_a_validator_exiting_nonzero_fails_with_its_exception_line(monkeypatch, tmp_path):
    stderr = 'INFO Reading\nException in thread "main" org.x.CsvEntityIOException: io error\n\tat x\n'

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", stderr)

    monkeypatch.setattr(rv.subprocess, "run", fake_run)
    with pytest.raises(rv.ValidatorFailed) as failed:
        rv.jar_validator(tmp_path / "validator.jar")(tmp_path, "10-20261110")
    assert (
        failed.value.reason == 'the validator exited 1: Exception in thread "main" org.x.CsvEntityIOException: io error'
    )


# ── the command line ─────────────────────────────────────────────────────────


def _listing(*keys: str) -> str:
    return "".join(f"2026-11-11 09:10:00     1234 {key}\n" for key in keys)


class _Store:
    """A fake `aws` CLI over an R2 listing, recording which credential pair
    each call ran under."""

    def __init__(self, tmp_path: Path, rt_agencies: tuple[int, ...] = (8, 10)):
        self.tmp_path = tmp_path
        self.keys = {
            "rt/": _listing(*(f"rt/{a}/20261110.tar.gz" for a in rt_agencies)),
            "static/": _listing("static/10/gtfs_static_20261010.zip", "static/8/gtfs_static_20261028.zip"),
        }
        self.calls: list[tuple[str, list[str]]] = []

    def capture(self, cmd) -> str:
        self.calls.append((os.environ.get("AWS_ACCESS_KEY_ID", ""), list(cmd)))
        prefix = cmd[3].split("/", 3)[3]
        return self.keys[prefix]

    def run(self, cmd) -> None:
        self.calls.append((os.environ.get("AWS_ACCESS_KEY_ID", ""), list(cmd)))
        source, dest = cmd[3], cmd[4]
        if source.startswith("s3://"):
            if source.endswith(".tar.gz"):
                _tar(Path(dest), _POLLS)
            else:
                _zip(Path(dest), _GTFS)


@pytest.fixture
def archive_env(monkeypatch):
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "transit-archives")
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-readonly-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-readonly-secret")
    for name in ("ML_STORE_BUCKET", "ML_STORE_ENDPOINT", "ML_STORE_ACCESS_KEY_ID", "ML_STORE_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def store_env(monkeypatch, archive_env):
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-write-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-write-secret")


def _main(store: _Store, tmp_path: Path, *args: str, validator=_deduplicating_validator, clock=None) -> int:
    argv = ["--date", "2026-11-10", "--out", str(tmp_path / "out"), "--work", str(tmp_path), *args]
    if clock is None:
        return rv.main(argv, run=store.run, capture=store.capture, validator=validator)
    return rv.main(argv, run=store.run, capture=store.capture, validator=validator, clock=clock)


def test_every_archive_is_read_before_the_write_pair_is_swapped_in(tmp_path, store_env):
    store = _Store(tmp_path)
    assert _main(store, tmp_path) == 0
    reads = [(pair, cmd) for pair, cmd in store.calls if "s3://transit-archives/" in " ".join(cmd)]
    writes = [(pair, cmd) for pair, cmd in store.calls if "s3://transit-ml/" in " ".join(cmd)]
    assert reads and {pair for pair, _ in reads} == {"archive-readonly-key"}
    assert [cmd[4] for _, cmd in writes] == [
        "s3://transit-ml/validation/8/20261110.json",
        "s3://transit-ml/validation/10/20261110.json",
    ]
    assert {pair for pair, _ in writes} == {"store-write-key"}
    assert store.calls.index(writes[0]) > store.calls.index(reads[-1])


def test_a_published_summary_is_removed_locally(tmp_path, store_env):
    assert _main(_Store(tmp_path), tmp_path) == 0
    assert not list((tmp_path / "out").rglob("*.json"))


def test_one_agency_failing_does_not_stop_the_others(tmp_path, archive_env):
    store = _Store(tmp_path)
    store.keys["static/"] = _listing("static/10/gtfs_static_20261010.zip")
    assert _main(store, tmp_path, "--no-upload") == 0
    eight = json.loads((tmp_path / "out" / "8" / "20261110.json").read_text())
    ten = json.loads((tmp_path / "out" / "10" / "20261110.json").read_text())
    assert eight["status"] == "no_timetable"
    assert ten["status"] == "ok" and ten["files_validated"] == 3


def test_the_run_fails_only_when_every_agency_failed(tmp_path, archive_env):
    def failing(work: Path, label: str) -> None:
        raise rv.ValidatorFailed("the validator exited 1")

    assert _main(_Store(tmp_path), tmp_path, "--no-upload", validator=failing) == 1


def test_an_agency_named_without_an_archive_is_stated_and_fails_the_run(tmp_path, archive_env):
    assert _main(_Store(tmp_path), tmp_path, "--no-upload", "--agency", "9") == 1
    doc = json.loads((tmp_path / "out" / "9" / "20261110.json").read_text())
    assert doc["status"] == "no_archive"


def test_no_upload_never_reads_the_write_pair(tmp_path, archive_env):
    store = _Store(tmp_path)
    assert _main(store, tmp_path, "--no-upload") == 0
    assert not [cmd for _, cmd in store.calls if "s3://transit-ml/" in " ".join(cmd)]
    assert os.environ["AWS_ACCESS_KEY_ID"] == "archive-readonly-key"


def test_a_dry_run_lists_the_plan_and_downloads_nothing(tmp_path, archive_env, capsys):
    store = _Store(tmp_path)
    assert _main(store, tmp_path, "--dry-run", validator=_never) == 0
    assert all(cmd[2] == "ls" for _, cmd in store.calls)
    out = capsys.readouterr().out
    assert "rt/10/20261110.tar.gz" in out and "static/10/gtfs_static_20261010.zip" in out
    assert not (tmp_path / "out").exists()


def test_a_jar_whose_hash_does_not_match_is_refused_before_any_read(tmp_path, archive_env):
    jar = tmp_path / "validator.jar"
    jar.write_bytes(b"not the validator")
    store = _Store(tmp_path)
    with pytest.raises(SystemExit):
        _main(store, tmp_path, "--no-upload", "--jar", str(jar))
    assert store.calls == []


def test_publishing_without_the_store_settings_stops_before_any_read(tmp_path, archive_env):
    store = _Store(tmp_path)
    with pytest.raises(SystemExit, match="ML_STORE_BUCKET"):
        _main(store, tmp_path)
    assert store.calls == []


def test_a_malformed_results_document_fails_only_its_agency(tmp_path, store_env):
    def validator(work: Path, label: str) -> None:
        if label.startswith("8-"):
            _writing(_MALFORMED)(work, label)
        else:
            _deduplicating_validator(work, label)

    store = _Store(tmp_path)
    assert _main(store, tmp_path, validator=validator) == 0
    published = [cmd[4] for _, cmd in store.calls if cmd[4].startswith("s3://transit-ml/")]
    assert published == [
        "s3://transit-ml/validation/8/20261110.json",
        "s3://transit-ml/validation/10/20261110.json",
    ]


def test_publishing_without_the_write_pair_stops_before_any_read(tmp_path, store_env, monkeypatch):
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "")
    store = _Store(tmp_path)
    with pytest.raises(SystemExit, match="ML_STORE_ACCESS_KEY_ID"):
        _main(store, tmp_path)
    assert store.calls == []
    assert os.environ["AWS_ACCESS_KEY_ID"] == "archive-readonly-key"


def test_no_agency_starts_once_the_run_budget_could_not_cover_its_validator(tmp_path, archive_env):
    started = []

    def validator(work: Path, label: str) -> None:
        started.append(label)
        _deduplicating_validator(work, label)

    readings = iter([0.0, rv.RUN_BUDGET_SEC - rv.TIMEOUT_SEC, rv.RUN_BUDGET_SEC - rv.TIMEOUT_SEC + 1])
    assert _main(_Store(tmp_path), tmp_path, "--no-upload", validator=validator, clock=lambda: next(readings)) == 0
    assert started == ["8-20261110"]
    ten = json.loads((tmp_path / "out" / "10" / "20261110.json").read_text())
    assert ten["status"] == "validator_failed" and ten["reason"].startswith("run budget exhausted")
    assert ten["files_given"] == 0 and ten["zip_key"] is None


def test_a_summary_that_cannot_be_removed_after_publishing_stops_no_other(tmp_path, store_env, monkeypatch):
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.parent.name == "8":
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    store = _Store(tmp_path)
    assert _main(store, tmp_path) == 0
    published = [cmd[4] for _, cmd in store.calls if cmd[4].startswith("s3://transit-ml/")]
    assert published == [
        "s3://transit-ml/validation/8/20261110.json",
        "s3://transit-ml/validation/10/20261110.json",
    ]
    assert not (tmp_path / "out" / "10" / "20261110.json").exists()


def test_a_summary_that_cannot_be_written_stops_no_other(tmp_path, store_env, monkeypatch):
    real_write_text = Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self.parent.name == "8":
            raise PermissionError(13, "Permission denied", str(self))
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)
    store = _Store(tmp_path)
    assert _main(store, tmp_path) == 0
    published = [cmd[4] for _, cmd in store.calls if cmd[4].startswith("s3://transit-ml/")]
    assert published == ["s3://transit-ml/validation/10/20261110.json"]


def test_a_work_directory_that_cannot_be_made_fails_only_its_agency(tmp_path, archive_env, monkeypatch):
    real = rv.tempfile.TemporaryDirectory
    calls = []

    def temporary_directory(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        return real(*args, **kwargs)

    monkeypatch.setattr(rv.tempfile, "TemporaryDirectory", temporary_directory)
    assert _main(_Store(tmp_path), tmp_path, "--no-upload") == 0
    eight = json.loads((tmp_path / "out" / "8" / "20261110.json").read_text())
    ten = json.loads((tmp_path / "out" / "10" / "20261110.json").read_text())
    assert eight["status"] == "validator_failed" and "No space left on device" in eight["reason"]
    assert ten["status"] == "ok"
