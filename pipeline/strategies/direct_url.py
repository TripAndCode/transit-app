"""Direct-URL static GTFS fetcher (Hiroshima-style).

For each agency:
  - GET <static_url> (treat as `current_data.zip`)
  - Also try <neighbour>/latest.zip — if its sha256 differs, prefer it (pre-cutover)
  - Persist as <dest_dir>/<agency_id>/gtfs_static_<YYYYMMDD>.zip
  - Stage the updated manifest beside <dest_dir>/<agency_id>/_manifest.json, and
    promote it once that zip has loaded (record_loaded)

Conditional GET via If-Modified-Since / If-None-Match. 304 → no-op.

static_url is the agency's configured source; latest.zip is a guess derived
from it, which not every host publishes. A failed latest.zip is only logged,
while a failed static_url raises StaticFetchError unless latest.zip delivered
a newer zip to load: reporting "no change" then would vouch for a source
nothing has heard from.
"""

import contextlib
import hashlib
import json
import logging
import os
import pathlib
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Optional

from pipeline.strategies.errors import FETCH_ERRORS, StaticFetchError, fetch_failed, require_http_url
from pipeline.url_guard import redact_url, safe_urlopen

logger = logging.getLogger(__name__)


def _sha256(path: pathlib.Path) -> str:
    """Return the hex SHA-256 digest of the file at path."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cond_get(url: str, manifest_entry: dict, dest: pathlib.Path) -> Optional[tuple[Optional[str], Optional[str]]]:
    """GET url with If-Modified-Since/If-None-Match from manifest_entry.

    Returns (last_modified, etag) on 200, once the body is written to dest,
    or None on 304. Raises StaticFetchError when the fetch failed.
    """
    req = urllib.request.Request(url)
    if manifest_entry.get("last_modified"):
        req.add_header("If-Modified-Since", manifest_entry["last_modified"])
    if manifest_entry.get("etag"):
        req.add_header("If-None-Match", manifest_entry["etag"])
    try:
        with safe_urlopen(req, timeout=30) as resp:
            data = resp.read()
            headers = resp.headers
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None
        raise fetch_failed(url, e) from e
    except FETCH_ERRORS as e:
        raise fetch_failed(url, e) from e
    dest.write_bytes(data)
    return headers.get("Last-Modified"), headers.get("ETag")


def _try_cond_get(
    url: str, manifest_entry: dict, dest: pathlib.Path
) -> tuple[Optional[tuple[Optional[str], Optional[str]]], Optional[StaticFetchError]]:
    """:func:`_cond_get`, with its failure returned rather than raised, so
    one variant failing still lets the other be fetched."""
    try:
        return _cond_get(url, manifest_entry, dest), None
    except StaticFetchError as e:
        return None, e


@contextlib.contextmanager
def _run_temps(agency_dir: pathlib.Path) -> Iterator[tuple[pathlib.Path, pathlib.Path]]:
    """Temp files for this run's current and latest downloads, removed when it ends.

    Unique to the run: two fetches of one agency that nothing serializes (two
    command-line runs, or one beside an admin press) share the agency
    directory, and neither may read, move or delete the other's download. A
    temp file that a run killed mid-fetch leaves behind is therefore never
    taken for a fresh download either, but nothing removes it.
    """
    made: list[pathlib.Path] = []
    try:
        for variant in ("current", "latest"):
            fd, name = tempfile.mkstemp(prefix=f"_tmp_{variant}_", suffix=".zip", dir=agency_dir)
            os.close(fd)
            made.append(pathlib.Path(name))
        yield made[0], made[1]
    finally:
        for path in made:
            path.unlink(missing_ok=True)


def fetch(
    agency_id: int,
    static_url: str,
    dest_dir: pathlib.Path,
) -> Optional[pathlib.Path]:
    """Fetch and persist the freshest GTFS zip for this agency.

    Returns the path of the zip ready for load_static, or None when nothing
    is newer. Raises StaticFetchError when static_url is not a valid
    http(s) URL or the fetch failed.
    """
    require_http_url(static_url, "the timetable URL")
    agency_dir = dest_dir / str(agency_id)
    agency_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(agency_dir)
    manifest = {}
    if manifest_path.exists():
        try:
            parsed = json.loads(manifest_path.read_text())
            manifest = parsed if isinstance(parsed, dict) else {}
        except (ValueError, OSError):
            # A manifest the promote-on-load rename should have kept valid,
            # but isn't (unreadable, not JSON, or not an object), is read the
            # same as a first-ever fetch for this agency: everything is new.
            manifest = {}

    # Derive latest_url from static_url by replacing the basename
    parsed = urllib.parse.urlparse(static_url)
    base = parsed.path.rsplit("/", 1)[0]
    latest_url = parsed._replace(path=f"{base}/latest.zip").geturl()

    with _run_temps(agency_dir) as (tmp_current, tmp_latest):
        cur_got, cur_failed = _try_cond_get(static_url, manifest.get("current", {}), tmp_current)
        lat_got, lat_failed = _try_cond_get(latest_url, manifest.get("latest", {}), tmp_latest)
        if lat_failed is not None:
            logger.warning(f"[direct_url] agency={agency_id} latest.zip failed, going on without it: {lat_failed}")
        if cur_failed is not None and lat_got is None:
            raise StaticFetchError("; ".join(str(e) for e in (cur_failed, lat_failed) if e is not None))
        cur_lm, cur_et = cur_got or (None, None)
        lat_lm, lat_et = lat_got or (None, None)

        # A variant's temp file holds a download only when that variant
        # answered 200; otherwise it is the empty file _run_temps made.
        cur_sha = _sha256(tmp_current) if cur_got is not None else manifest.get("current", {}).get("sha256")
        lat_sha = _sha256(tmp_latest) if lat_got is not None else manifest.get("latest", {}).get("sha256")

        prev_cur_sha = manifest.get("current", {}).get("sha256")
        prev_lat_sha = manifest.get("latest", {}).get("sha256")
        nothing_changed = (cur_sha is None or cur_sha == prev_cur_sha) and (lat_sha is None or lat_sha == prev_lat_sha)
        if nothing_changed:
            if cur_failed is not None:
                # latest.zip answered, but only with what already loaded.
                raise cur_failed
            logger.info(f"[direct_url] agency={agency_id} no change")
            return None
        if cur_failed is not None:
            logger.warning(f"[direct_url] agency={agency_id} static URL failed, loading latest.zip: {cur_failed}")

        # Something changed, so at least one variant downloaded this run.
        # latest.zip wins when it downloaded and differs from current
        # (pre-cutover), or when it is the only one that downloaded.
        prefer_latest = lat_got is not None and (cur_got is None or lat_sha != cur_sha)
        chosen_tmp = tmp_latest if prefer_latest else tmp_current

        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        final = agency_dir / f"gtfs_static_{day}.zip"
        chosen_tmp.replace(final)

    manifest["current"] = {
        # Redacted: static_url routinely carries an API key in the query
        # string (ODPT and similar JP GTFS providers). The manifest only
        # needs this for logging/identity, not re-fetching — re-fetch
        # always reads the live static_url from the DB.
        "url": redact_url(static_url),
        "last_modified": cur_lm or manifest.get("current", {}).get("last_modified"),
        "etag": cur_et or manifest.get("current", {}).get("etag"),
        "sha256": cur_sha,
    }
    manifest["latest"] = {
        "url": redact_url(latest_url),
        "last_modified": lat_lm or manifest.get("latest", {}).get("last_modified"),
        "etag": lat_et or manifest.get("latest", {}).get("etag"),
        "sha256": lat_sha,
    }
    # Staged, not written: the manifest is what the next fetch's conditional
    # GET and "no change" both read, so it may describe only a zip that loaded.
    _pending_path(agency_dir).write_text(json.dumps(manifest, indent=2))
    logger.info(f"[direct_url] agency={agency_id} persisted {final.name}")
    return final


def record_loaded(agency_id: int, dest_dir: pathlib.Path, zip_path: pathlib.Path) -> None:
    """Promote the manifest fetch() staged for *zip_path*, now that it loaded."""
    agency_dir = dest_dir / str(agency_id)
    pending = _pending_path(agency_dir)
    if pending.exists():
        pending.replace(_manifest_path(agency_dir))


def _manifest_path(agency_dir: pathlib.Path) -> pathlib.Path:
    return agency_dir / "_manifest.json"


def _pending_path(agency_dir: pathlib.Path) -> pathlib.Path:
    return agency_dir / "_manifest.pending.json"
