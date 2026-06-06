"""Aomori static fetcher (HTML index scrape).

Mirrors the existing oracle_cloud/poller_static.sh: GET the opendata index
page, find the first `gtfs-aomoricitybus*.zip` href, resolve it relative to
the site root, download, sha256, persist as gtfs_static_YYYYMMDD.zip.
"""

import hashlib
import logging
import pathlib
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_HREF_RE = re.compile(r'href="([^"]*gtfs-aomoricitybus[^"]*\.zip)"')


def _sha256(path: pathlib.Path) -> str:
    """Return the hex SHA-256 digest of the file at path."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve(href: str, index_url: str) -> str:
    """Resolve a potentially-relative href against the index page URL."""
    if href.startswith(("http://", "https://")):
        return href
    parsed = urllib.parse.urlparse(index_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    if href.startswith("/"):
        return root + href
    return f"{root}{parsed.path.rsplit('/', 1)[0]}/{href}"


def fetch(
    agency_id: int,
    index_url: str,
    dest_dir: pathlib.Path,
) -> Optional[pathlib.Path]:
    """Fetch and persist the freshest GTFS zip for Aomori.

    Returns the path of the zip ready for load_static, or None on failure.
    Idempotent same-day overwrite (matches existing shell behaviour).
    """
    agency_dir = dest_dir / str(agency_id)
    agency_dir.mkdir(parents=True, exist_ok=True)

    try:
        with urllib.request.urlopen(index_url, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        logger.warning(f"[aomori_index_scrape] failed to fetch index {index_url}: {e}")
        return None

    m = _HREF_RE.search(html)
    if not m:
        logger.warning("[aomori_index_scrape] gtfs-aomoricitybus*.zip href not found")
        return None
    zip_url = _resolve(m.group(1), index_url)

    day = datetime.now().strftime("%Y%m%d")
    final = agency_dir / f"gtfs_static_{day}.zip"
    try:
        with urllib.request.urlopen(zip_url, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        logger.warning(f"[aomori_index_scrape] failed to fetch zip {zip_url}: {e}")
        return None

    if data[:2] != b"PK":
        logger.warning("[aomori_index_scrape] downloaded file is not a ZIP (missing PK header)")
        return None

    final.write_bytes(data)
    sha = _sha256(final)
    history_path = agency_dir / "fetch_history.csv"
    if not history_path.exists():
        history_path.write_text("timestamp,zip_url,sha256,bytes,file_path\n")
    with history_path.open("a") as f:
        f.write(f"{datetime.now().isoformat()},{zip_url},{sha},{len(data)},{final}\n")

    logger.info(f"[aomori_index_scrape] agency={agency_id} persisted {final.name} (sha256={sha[:12]})")
    return final
