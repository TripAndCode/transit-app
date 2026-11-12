"""Run MobilityData's GTFS-RT validator on one sampled hour of each agency's
archived TripUpdates, and publish its counts per rule.

For service date D (default: yesterday, JST) and each agency R2 holds realtime
archives for (or each --agency):

- **The sample** is the polls of 09:00 up to 10:00 JST on D, taken from R2's
  `rt/<id>/<YYYYMMDD>.tar.gz` for the UTC day the window starts on, which is D:
  the collector names an archive by the UTC day it polled, so archive D opens
  at 09:00 JST on D. Each poll is timed by its member name,
  `YYYYMMDD/TripUpdate_HHMMSS.pb`, which the collector writes in UTC, and is
  renamed `TripUpdate-YYYY-MM-DDTHH-MM-SSZ.pb`, the form the validator's
  `-sort name` reads as the poll's current time. Without it the validator
  takes a file's modification time instead, and its freshness and timestamp
  rules would judge each poll against the time it was unpacked.
- **The timetable** is the newest R2 `static/<id>/gtfs_static_YYYYMMDD.zip`
  dated on or before D, the rule `scripts/catch_up_from_r2.py` dates
  timetables by. Only the core GTFS files the validator reads are passed to
  it (GTFS_CORE_FILES): its reader rejects the legacy GTFS-JP
  translations.txt.
- **The validator** is the official image pinned by digest (IMAGE), run with no
  network and no credentials, its heap capped at HEAP_MAX_MB, or with --jar the
  same jar on a local JVM, refused unless its sha256 is JAR_SHA256. It writes
  one results file per poll it validated and skips a poll byte-identical to
  the one before it, or one that is not a FeedMessage, so `files_validated`
  can be below `files_given`. Each rule's firings are counted from those
  files, and they are deleted with the rest of the work directory.

One summary per agency and date (summary(), schema SCHEMA) is written to
`<out>/<id>/<YYYYMMDD>.json` and published to the transit-ml bucket at
`validation/<id>/<YYYYMMDD>.json` (summary_key), then removed locally; one
whose upload fails stays. A run that fails for one agency is that agency's
status (STATUSES) and reason, and never stops the others; so is a validator
that wrote no results, or results of a shape rule_counts cannot read. Agencies
run one after another within RUN_BUDGET_SEC: one starts only while its
validator's TIMEOUT_SEC still fits, and each left over is stated as
validator_failed, "run budget exhausted". The run exits 1 only when no
agency's summary is ok and published.

Credentials: transit-archives is read with the AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY pair in the environment, OBJECT_STORE_BUCKET and
OBJECT_STORE_ENDPOINT, as `ml.cli sync` reads it; the transit-ml pair
(ML_STORE_*) is swapped in only after every archive read
(ml.r2.use_store_credentials). --no-upload and --dry-run never read ML_STORE_*.
On a developer machine, export `.env`'s OBJECT_STORE_BUCKET and
OBJECT_STORE_ENDPOINT, and its OBJECT_STORE_ACCESS_KEY_ID and
OBJECT_STORE_SECRET_ACCESS_KEY as that AWS_* pair, and pass --no-upload.

Usage:
    python scripts/rt_validate_sample.py [--date YYYY-MM-DD] [--agency ID ...]
        [--out DIR] [--work DIR] [--jar PATH] [--no-upload] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.r2 import (  # noqa: E402  (sys.path injection above)
    default_capture,
    default_run,
    download_file,
    upload_file,
    use_store_credentials,
)
from ml.sync import Archive, list_keys, parse_keys  # noqa: E402

SCHEMA = 1

# The official image by its index digest; REVISION is the source commit it was
# built from and JAR_SHA256 the jar inside it. The project publishes no
# versioned release, so these three are the validator's identity.
IMAGE_REPOSITORY = "ghcr.io/mobilitydata/gtfs-realtime-validator"
IMAGE_DIGEST = "sha256:ef437eb291259ba16a3a4a0e9a83b18f597a365e3a0fdc9a8a5b73d4a541333d"
IMAGE = f"{IMAGE_REPOSITORY}@{IMAGE_DIGEST}"
REVISION = "57146b4eb7a55e68f4655b8c3f3ffb5ed9cebff1"
JAR_SHA256 = "50d038cce9de455051f74604eb46e2717ec8fa29a166d0a13a4d0f17d364260b"
IMAGE_JAR = "/app/gtfs-realtime-validator-webapp-1.0.0-SNAPSHOT.jar"
# The batch processor; the jar's own Main-Class starts the web app instead.
MAIN_CLASS = "edu.usf.cutr.gtfsrtvalidator.lib.Main"
HEAP_MAX_MB = 512
CONTAINER_MEMORY = "1g"
# docker runs the JVM outside the calling unit's cgroup, so the unit's Nice=
# never reaches it; the container's own CPU cap does.
CONTAINER_CPUS = "2"
TIMEOUT_SEC = 300
# The whole run's wall time: an agency starts only while its validator's
# TIMEOUT_SEC still fits, so the run ends before the replica sync that follows
# it on the VPS (deploy/vps/systemd/transit-ml-validate.timer).
RUN_BUDGET_SEC = 40 * 60

JST = ZoneInfo("Asia/Tokyo")
WINDOW_START_JST = clock(9, 0)
WINDOW_LENGTH = timedelta(hours=1)

GTFS_CORE_FILES = frozenset(
    {
        "agency.txt",
        "stops.txt",
        "routes.txt",
        "trips.txt",
        "stop_times.txt",
        "calendar.txt",
        "calendar_dates.txt",
        "shapes.txt",
        "frequencies.txt",
        "feed_info.txt",
    }
)
RESULTS_SUFFIX = ".results.json"

Status = Literal["ok", "no_archive", "no_timetable", "validator_failed"]
STATUSES: tuple[Status, ...] = ("ok", "no_archive", "no_timetable", "validator_failed")
Runner = Literal["docker", "jar"]

Fetch = Callable[[str, Path], None]
RunValidator = Callable[[Path, str], None]


class ValidatorFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Window:
    """Polls from ``start`` up to but not including ``end``, both UTC."""

    start: datetime
    end: datetime


def sample_window(service_date: date) -> Window:
    start = datetime.combine(service_date, WINDOW_START_JST, tzinfo=JST).astimezone(timezone.utc)
    return Window(start, start + WINDOW_LENGTH)


def poll_instant(member_name: str) -> datetime | None:
    """The UTC instant an archive member's name records, or None for any name
    not exactly `YYYYMMDD/TripUpdate_HHMMSS.pb` naming a real day and time."""
    path = PurePosixPath(member_name)
    try:
        parsed = datetime.strptime(f"{path.parent.name}/{path.name}", "%Y%m%d/TripUpdate_%H%M%S.pb")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def window_members(names: Iterable[str], window: Window) -> list[tuple[str, datetime]]:
    timed = ((name, poll_instant(name)) for name in names)
    inside = [(name, at) for name, at in timed if at is not None and window.start <= at < window.end]
    return sorted(inside, key=lambda member: member[1])


def validator_name(instant: datetime) -> str:
    return f"TripUpdate-{instant.astimezone(timezone.utc):%Y-%m-%dT%H-%M-%S}Z.pb"


def stage_window(archive: Path, dest: Path, window: Window) -> int:
    """Write each poll of *archive* inside *window* to *dest* under its
    validator name; the number of files written."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        members = {m.name: m for m in tf.getmembers() if m.isfile()}
        written = set()
        for name, instant in window_members(members, window):
            source = tf.extractfile(members[name])
            if source is None:
                continue
            target = validator_name(instant)
            (dest / target).write_bytes(source.read())
            written.add(target)
    return len(written)


def trim_gtfs(source: Path, dest: Path) -> list[str]:
    """Copy *source* to *dest* with only its top-level GTFS_CORE_FILES; the names kept."""
    kept = []
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if name in GTFS_CORE_FILES:
                with zin.open(name) as src, zout.open(name, "w") as dst:
                    shutil.copyfileobj(src, dst)
                kept.append(name)
    return sorted(kept)


def archive_for(archives: Iterable[Archive], agency_id: int, day: date) -> Archive | None:
    return next((a for a in archives if a.kind == "rt" and a.agency_id == agency_id and a.day == day), None)


def timetable_for(archives: Iterable[Archive], agency_id: int, day: date) -> Archive | None:
    dated = [a for a in archives if a.kind == "static" and a.agency_id == agency_id and a.day <= day]
    return max(dated, key=lambda a: a.day, default=None)


def rule_counts(results: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Per rule over one results document per validated poll: its occurrences
    and the number of polls it fired in. A rule listed with no occurrence did
    not fire."""
    rules: dict[str, dict[str, Any]] = {}
    for document in results:
        fired_here = set()
        for item in document:
            occurrences = len(item["occurrenceList"])
            if not occurrences:
                continue
            rule = item["errorMessage"]["validationRule"]
            entry = rules.setdefault(
                rule["errorId"],
                {
                    "rule_id": rule["errorId"],
                    "severity": rule["severity"],
                    "title": rule["title"],
                    "occurrences": 0,
                    "files_with_rule": 0,
                },
            )
            entry["occurrences"] += occurrences
            fired_here.add(rule["errorId"])
        for rule_id in fired_here:
            rules[rule_id]["files_with_rule"] += 1
    return [rules[rule_id] for rule_id in sorted(rules)]


def validator_identity(runner: Runner) -> dict[str, Any]:
    return {
        "image": IMAGE,
        "revision": REVISION,
        "jar_sha256": JAR_SHA256,
        "runner": runner,
        "heap_max_mb": HEAP_MAX_MB,
    }


def _utc_text(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summary(
    *,
    agency_id: int,
    service_date: date,
    validator: dict[str, Any],
    status: str,
    reason: str | None = None,
    files_given: int = 0,
    files_validated: int = 0,
    zip_key: str | None = None,
    runtime_sec: float | None = None,
    rules: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """One agency's summary for one date. ``reason`` is set exactly when the
    status is not ok, and ``rules`` is empty unless it is: a rule absent from
    an ok summary did not fire on any validated poll. ``window_end_utc`` is
    exclusive."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if (reason is None) != (status == "ok"):
        raise ValueError("a summary states a reason exactly when its status is not ok")
    if rules and status != "ok":
        raise ValueError("only an ok summary carries rule counts")
    window = sample_window(service_date)
    return {
        "schema": SCHEMA,
        "agency_id": agency_id,
        "service_date": service_date.isoformat(),
        "window_start_utc": _utc_text(window.start),
        "window_end_utc": _utc_text(window.end),
        "files_given": files_given,
        "files_validated": files_validated,
        "zip_key": zip_key,
        "validator": validator,
        "runtime_sec": runtime_sec,
        "status": status,
        "reason": reason,
        "rules": list(rules),
    }


def summary_key(agency_id: int, day: date) -> str:
    return f"validation/{agency_id}/{day:%Y%m%d}.json"


def failure_line(stderr: str) -> str:
    """The line that best names why the validator failed: its first exception
    line, else its last line of output."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("Exception in thread") or (line.split(":", 1)[0].endswith("Exception")):
            return line[:300]
    return lines[-1][:300] if lines else "no output"


def _validator_args(jar: str, gtfs: str, realtime: str) -> list[str]:
    return [
        f"-Xmx{HEAP_MAX_MB}m",
        "-cp",
        jar,
        MAIN_CLASS,
        "-gtfs",
        gtfs,
        "-gtfsRealtimePath",
        realtime,
        "-sort",
        "name",
    ]


def docker_command(work: Path, name: str) -> list[str]:
    """The pinned image over *work*, mounted at /data, with no network and no
    environment of this process: the container never sees a credential."""
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--memory",
        CONTAINER_MEMORY,
        "--cpus",
        CONTAINER_CPUS,
        "-v",
        f"{work}:/data",
        "--entrypoint",
        "java",
        IMAGE,
        *_validator_args(IMAGE_JAR, "/data/gtfs.zip", "/data/rt"),
    ]


def jar_command(jar: Path, work: Path) -> list[str]:
    return ["java", *_validator_args(str(jar), str(work / "gtfs.zip"), str(work / "rt"))]


def _run_validator_command(cmd: list[str], *, on_timeout: Callable[[], None] | None = None) -> None:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        if on_timeout is not None:
            on_timeout()
        raise ValidatorFailed(f"the validator ran past {TIMEOUT_SEC} s") from exc
    if done.returncode != 0:
        raise ValidatorFailed(f"the validator exited {done.returncode}: {failure_line(done.stderr)}")


def docker_validator(work: Path, label: str) -> None:
    name = f"rt-validate-{label}-{os.getpid()}"

    def remove() -> None:
        # Killing the docker client on a timeout leaves its container running.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

    _run_validator_command(docker_command(work, name), on_timeout=remove)


def jar_validator(jar: Path) -> RunValidator:
    def run(work: Path, label: str) -> None:
        _run_validator_command(jar_command(jar, work))

    return run


def validate_agency(
    agency_id: int,
    service_date: date,
    archives: Sequence[Archive],
    *,
    fetch: Fetch,
    run_validator: RunValidator,
    runner: Runner,
    work_root: Path | None = None,
) -> dict[str, Any]:
    """One agency's summary for *service_date*; every failure is its status."""
    window = sample_window(service_date)
    identity = validator_identity(runner)

    def stated(status: Status, reason: str | None = None, **fields: Any) -> dict[str, Any]:
        return summary(
            agency_id=agency_id, service_date=service_date, validator=identity, status=status, reason=reason, **fields
        )

    archive = archive_for(archives, agency_id, window.start.date())
    if archive is None:
        return stated("no_archive", f"R2 holds no rt/{agency_id}/{window.start:%Y%m%d}.tar.gz")
    timetable = timetable_for(archives, agency_id, service_date)
    if timetable is None:
        return stated("no_timetable", f"R2 holds no static/{agency_id}/ timetable dated on or before {service_date}")

    files_given = 0
    runtime_sec: float | None = None
    step = f"downloading {archive.key}"
    with tempfile.TemporaryDirectory(dir=work_root, prefix=f"rt-validate-{agency_id}-") as tmp:
        work = Path(tmp)
        realtime = work / "rt"
        try:
            fetch(archive.key, work / "archive.tar.gz")
            step = f"reading {archive.key}"
            files_given = stage_window(work / "archive.tar.gz", realtime, window)
            if not files_given:
                return stated(
                    "no_archive",
                    f"{archive.key} holds no TripUpdate poll from {_utc_text(window.start)} to {_utc_text(window.end)}",
                )
            step = f"downloading {timetable.key}"
            fetch(timetable.key, work / "timetable.zip")
            step = f"trimming {timetable.key}"
            trim_gtfs(work / "timetable.zip", work / "gtfs.zip")
            step = "running the validator"
            started = time.monotonic()
            try:
                run_validator(work, f"{agency_id}-{service_date:%Y%m%d}")
            finally:
                runtime_sec = round(time.monotonic() - started, 1)
            step = "reading the validator's results"
            results = [
                json.loads(p.read_text(encoding="utf-8")) for p in sorted(realtime.glob(f"*.pb{RESULTS_SUFFIX}"))
            ]
            rules = rule_counts(results)
        except ValidatorFailed as exc:
            return stated(
                "validator_failed", exc.reason, files_given=files_given, zip_key=timetable.key, runtime_sec=runtime_sec
            )
        except subprocess.CalledProcessError as exc:
            # The command line carries the bucket's endpoint; the exit status is enough.
            reason = f"{step} failed (exit {exc.returncode})"
            return stated("validator_failed", reason, files_given=files_given, zip_key=timetable.key)
        except Exception as exc:
            reason = f"{step} failed: {type(exc).__name__}: {exc}"[:300]
            return stated(
                "validator_failed", reason, files_given=files_given, zip_key=timetable.key, runtime_sec=runtime_sec
            )
    if not results:
        # The first poll is never skipped as a duplicate, so no results means the validator read no poll.
        polls = f"{files_given} poll{'' if files_given == 1 else 's'}"
        return stated(
            "validator_failed",
            f"the validator wrote no results for {polls}",
            files_given=files_given,
            zip_key=timetable.key,
            runtime_sec=runtime_sec,
        )
    return stated(
        "ok",
        files_given=files_given,
        files_validated=len(results),
        zip_key=timetable.key,
        runtime_sec=runtime_sec,
        rules=rules,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} must be set, not empty (--no-upload needs no ML_STORE_*)")
    return value


def _yesterday_jst() -> date:
    return datetime.now(JST).date() - timedelta(days=1)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0] if __doc__ else None)
    parser.add_argument("--date", type=date.fromisoformat, help="service date (default: yesterday, JST)")
    parser.add_argument("--agency", type=int, action="append", help="agency id (repeatable; default: every rt/<id>/)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / ".cache" / "transit-app" / "rt-validation",
        help="where summaries are written, as <id>/<YYYYMMDD>.json",
    )
    parser.add_argument("--work", type=Path, help="parent of each agency's temporary work directory")
    parser.add_argument("--jar", type=Path, help="run this validator jar on a local java instead of the image")
    parser.add_argument("--no-upload", action="store_true", help="write the summaries locally only")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; download and write nothing")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    run: Callable[[Sequence[str]], None] = default_run,
    capture: Callable[[Sequence[str]], str] = default_capture,
    validator: RunValidator | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    started = clock()
    args = _parse_args(argv)
    runner: Runner = "jar" if args.jar else "docker"
    if args.jar and _sha256(args.jar) != JAR_SHA256:
        raise SystemExit(f"{args.jar} is not the pinned validator jar (sha256 {JAR_SHA256})")
    service_date = args.date or _yesterday_jst()
    bucket, endpoint = _required("OBJECT_STORE_BUCKET"), _required("OBJECT_STORE_ENDPOINT")
    upload = not (args.no_upload or args.dry_run)
    if upload:
        store_bucket, store_endpoint = _required("ML_STORE_BUCKET"), _required("ML_STORE_ENDPOINT")
        # Checked now so a misconfigured env fails before any work, but swapped
        # in only after the last archive read (use_store_credentials).
        _required("ML_STORE_ACCESS_KEY_ID")
        _required("ML_STORE_SECRET_ACCESS_KEY")

    archives = parse_keys(list_keys(bucket, endpoint, capture))
    agency_ids = args.agency or sorted({a.agency_id for a in archives if a.kind == "rt"})
    window = sample_window(service_date)
    if args.dry_run:
        for agency_id in agency_ids:
            archive = archive_for(archives, agency_id, window.start.date())
            timetable = timetable_for(archives, agency_id, service_date)
            print(
                f"agency {agency_id} {service_date}: {archive.key if archive else 'no archive'} "
                f"{_utc_text(window.start)}..{_utc_text(window.end)} with "
                f"{timetable.key if timetable else 'no timetable'} -> {summary_key(agency_id, service_date)}"
            )
        return 0

    run_validator = validator or (jar_validator(args.jar) if args.jar else docker_validator)

    def fetch(key: str, dest: Path) -> None:
        download_file(bucket, key, dest, endpoint, run=run)

    if args.work:
        args.work.mkdir(parents=True, exist_ok=True)
    written: dict[int, Path] = {}
    ok: set[int] = set()
    for agency_id in agency_ids:
        if clock() - started + TIMEOUT_SEC > RUN_BUDGET_SEC:
            doc = summary(
                agency_id=agency_id,
                service_date=service_date,
                validator=validator_identity(runner),
                status="validator_failed",
                reason=f"run budget exhausted: the run's {RUN_BUDGET_SEC} s left no room for this agency",
            )
        else:
            doc = validate_agency(
                agency_id,
                service_date,
                archives,
                fetch=fetch,
                run_validator=run_validator,
                runner=runner,
                work_root=args.work,
            )
        path = args.out / str(agency_id) / f"{service_date:%Y%m%d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1) + "\n")
        written[agency_id] = path
        if doc["status"] == "ok":
            ok.add(agency_id)
        print(
            f"agency {agency_id} {service_date}: {doc['status']}, "
            f"{doc['files_validated']}/{doc['files_given']} polls validated"
            + (f" ({doc['reason']})" if doc["reason"] else "")
        )

    if upload:
        use_store_credentials()
        for agency_id, path in written.items():
            published = False
            try:
                upload_file(path, store_bucket, summary_key(agency_id, service_date), store_endpoint, run=run)
                published = True
                path.unlink()
            except (subprocess.CalledProcessError, OSError) as exc:
                # A failed command's text carries the bucket's endpoint; its exit status is enough.
                detail = f"exit {exc.returncode}" if isinstance(exc, subprocess.CalledProcessError) else str(exc)
                step = "removing it after publishing" if published else "publishing it"
                sys.stderr.write(f"agency {agency_id}: {step} ({path}) failed: {detail}\n")
                if not published:
                    ok.discard(agency_id)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
