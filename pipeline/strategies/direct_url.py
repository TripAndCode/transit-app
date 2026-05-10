"""Direct-URL static GTFS fetcher (Hiroshima-style).

For each agency:
  - GET <static_url> (treat as `current_data.zip`)
  - Also try <neighbour>/latest.zip — if its sha256 differs, prefer it (pre-cutover)
  - Persist as <dest_dir>/<agency_id>/gtfs_static_<YYYYMMDD>.zip
  - Update manifest at <dest_dir>/<agency_id>/_manifest.json

Conditional GET via If-Modified-Since / If-None-Match. 304 → no-op.
"""

import hashlib
import json
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cond_get(url: str, manifest_entry: dict, dest: pathlib.Path) -> tuple[Optional[str], Optional[str]]:
    """GET url with If-Modified-Since/If-None-Match from manifest_entry.

    Returns (last_modified, etag) on 200 (and writes dest), or (None, None) on
    304 / network failure.
    """
    req = urllib.request.Request(url)
    if manifest_entry.get("last_modified"):
        req.add_header("If-Modified-Since", manifest_entry["last_modified"])
    if manifest_entry.get("etag"):
        req.add_header("If-None-Match", manifest_entry["etag"])
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            with dest.open("wb") as f:
                f.write(data)
            return resp.headers.get("Last-Modified"), resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, None
        print(f"[direct_url] HTTP {e.code} for {url}: {e.reason}")
        return None, None
    except urllib.error.URLError as e:
        print(f"[direct_url] network error for {url}: {e}")
        return None, None


def fetch(
    agency_id: int,
    static_url: str,
    dest_dir: pathlib.Path,
) -> Optional[pathlib.Path]:
    """Fetch and persist the freshest GTFS zip for this agency.

    Returns the path of the zip ready for load_static, or None if no change.
    """
    agency_dir = dest_dir / str(agency_id)
    agency_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = agency_dir / "_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    # Derive latest_url from static_url by replacing the basename
    parsed = urllib.parse.urlparse(static_url)
    base = parsed.path.rsplit("/", 1)[0]
    latest_url = parsed._replace(path=f"{base}/latest.zip").geturl()

    tmp_current = agency_dir / "_tmp_current.zip"
    tmp_latest = agency_dir / "_tmp_latest.zip"

    cur_lm, cur_et = _cond_get(static_url, manifest.get("current", {}), tmp_current)
    lat_lm, lat_et = _cond_get(latest_url, manifest.get("latest", {}), tmp_latest)

    cur_sha = _sha256(tmp_current) if tmp_current.exists() else manifest.get("current", {}).get("sha256")
    lat_sha = _sha256(tmp_latest) if tmp_latest.exists() else manifest.get("latest", {}).get("sha256")

    nothing_changed = (
        cur_lm is None and lat_lm is None
        and cur_sha == manifest.get("current", {}).get("sha256")
        and lat_sha == manifest.get("latest", {}).get("sha256")
    )
    if nothing_changed:
        for tmp in (tmp_current, tmp_latest):
            tmp.unlink(missing_ok=True)
        print(f"[direct_url] agency={agency_id} no change")
        return None

    # Pick which to load: prefer latest if it differs from current
    chosen_tmp = tmp_latest if (lat_sha and cur_sha and lat_sha != cur_sha) else tmp_current
    if not chosen_tmp.exists():
        # one variant 304'd, fall back to whichever did download
        chosen_tmp = tmp_current if tmp_current.exists() else tmp_latest

    if not chosen_tmp.exists():
        print(f"[direct_url] agency={agency_id} both variants 304/failed — keeping prior state")
        return None

    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    final = agency_dir / f"gtfs_static_{day}.zip"
    chosen_tmp.replace(final)
    # Clean up the other tmp if it still exists
    for tmp in (tmp_current, tmp_latest):
        tmp.unlink(missing_ok=True)

    manifest["current"] = {
        "url": static_url,
        "last_modified": cur_lm or manifest.get("current", {}).get("last_modified"),
        "etag": cur_et or manifest.get("current", {}).get("etag"),
        "sha256": cur_sha,
    }
    manifest["latest"] = {
        "url": latest_url,
        "last_modified": lat_lm or manifest.get("latest", {}).get("last_modified"),
        "etag": lat_et or manifest.get("latest", {}).get("etag"),
        "sha256": lat_sha,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[direct_url] agency={agency_id} persisted {final.name}")
    return final
