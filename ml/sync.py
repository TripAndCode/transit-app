"""Mirror R2's archives into the ML replica.

The bucket holds `rt/<agency>/<YYYYMMDD>.tar.gz`, one GTFS-RT archive per UTC
day, and `static/<agency>/gtfs_static_<YYYYMMDD>.zip`, one per timetable the
collector saw. The replica replays them in timeline order, so every realtime
day joins the timetable it ran under.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

_RT_KEY = re.compile(r"^rt/(?P<agency>\d+)/(?P<day>\d{8})\.tar\.gz$")
_STATIC_KEY = re.compile(r"^static/(?P<agency>\d+)/gtfs_static_(?P<day>\d{8})\.zip$")


@dataclass(frozen=True)
class Archive:
    agency_id: int
    day: date
    key: str
    kind: Literal["rt", "static"]


@dataclass(frozen=True)
class LoadStatic:
    archive: Archive


@dataclass(frozen=True)
class IngestDays:
    agency_id: int
    archives: tuple[Archive, ...]


Action = LoadStatic | IngestDays


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y%m%d").date()


def parse_keys(keys: Iterable[str]) -> list[Archive]:
    archives: list[Archive] = []
    for key in keys:
        if match := _RT_KEY.match(key):
            archives.append(Archive(int(match["agency"]), _day(match["day"]), key, "rt"))
        elif match := _STATIC_KEY.match(key):
            archives.append(Archive(int(match["agency"]), _day(match["day"]), key, "static"))
    return archives


def is_final(archive: Archive, today_jst: date) -> bool:
    """A realtime archive runs to 09:00 JST the day after its UTC day, and
    ingest leaves the rows of a JST day that has not ended for a later run.
    All its rows are in only once that following JST day has ended."""
    return archive.kind == "static" or today_jst >= archive.day + timedelta(days=2)


def plan(archives: Iterable[Archive], done: AbstractSet[str]) -> list[Action]:
    by_agency: dict[int, list[Archive]] = defaultdict(list)
    for archive in archives:
        by_agency[archive.agency_id].append(archive)
    actions: list[Action] = []
    for agency_id in sorted(by_agency):
        # Same day: the timetable first, so that day's realtime joins it.
        timeline = sorted(by_agency[agency_id], key=lambda a: (a.day, a.kind != "static"))
        batch: list[Archive] = []
        for archive in timeline:
            if archive.key in done:
                continue
            if archive.kind == "static":
                if batch:
                    actions.append(IngestDays(agency_id, tuple(batch)))
                    batch = []
                actions.append(LoadStatic(archive))
            else:
                batch.append(archive)
        if batch:
            actions.append(IngestDays(agency_id, tuple(batch)))
    return actions


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text())["done"])


def save_done(path: Path, done: AbstractSet[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps({"done": sorted(done)}))
    os.replace(partial, path)


Runner = Callable[[Sequence[str]], None]


def list_keys(bucket: str, endpoint: str, capture: Callable[[Sequence[str]], str]) -> list[str]:
    keys: list[str] = []
    for prefix in ("rt/", "static/"):
        output = capture(["aws", "s3", "ls", f"s3://{bucket}/{prefix}", "--recursive", "--endpoint-url", endpoint])
        keys.extend(line.split(None, 3)[3] for line in output.splitlines() if len(line.split(None, 3)) == 4)
    return keys


def _download(archive: Archive, dest_dir: Path, *, bucket: str, endpoint: str, run: Runner) -> Path:
    dest = dest_dir / Path(archive.key).name
    run(
        ["aws", "s3", "cp", f"s3://{bucket}/{archive.key}", str(dest), "--endpoint-url", endpoint, "--only-show-errors"]
    )
    return dest


def execute(
    actions: Sequence[Action],
    *,
    bucket: str,
    endpoint: str,
    work_dir: Path,
    state_path: Path,
    done: set[str],
    today_jst: date,
    run: Runner,
    python: str,
) -> list[str]:
    """Run the plan in order, saving the done-set after every action. A
    failure stops that agency's timeline, since a later day may need what
    failed, and leaves the other agencies running."""
    failures: list[str] = []
    stopped: set[int] = set()
    for action in actions:
        agency_id = action.archive.agency_id if isinstance(action, LoadStatic) else action.agency_id
        if agency_id in stopped:
            continue
        scratch = work_dir / str(agency_id)
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            if isinstance(action, LoadStatic):
                path = _download(action.archive, scratch, bucket=bucket, endpoint=endpoint, run=run)
                run([python, "gtfs_pipeline.py", "load_static", str(path), "--agency-id", str(agency_id)])
                done.add(action.archive.key)
            else:
                for archive in action.archives:
                    _download(archive, scratch, bucket=bucket, endpoint=endpoint, run=run)
                run([python, "gtfs_pipeline.py", "ingest", str(scratch), "--agency-id", str(agency_id)])
                done.update(a.key for a in action.archives if is_final(a, today_jst))
            save_done(state_path, done)
        except subprocess.CalledProcessError as error:
            failures.append(f"agency {agency_id}: {' '.join(map(str, error.cmd[:3]))} exited {error.returncode}")
            stopped.add(agency_id)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return failures
