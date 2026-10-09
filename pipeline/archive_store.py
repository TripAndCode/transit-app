"""Archive ingest straight from the object store the collector syncs to.

The collector uploads one tarball per agency per UTC day to an S3-compatible
bucket (Cloudflare R2) as `rt/<agency_id>/YYYYMMDD.tar.gz`, and each new static
GTFS version as `static/<agency_id>/gtfs_static_YYYYMMDD.zip`.
`ingest_from_store` downloads each day in a range and hands it to
`pipeline.ingest.ingest` one day at a time, so a run needs disk for one tarball
and every guarantee of archive ingest holds unchanged: a file already in
`updates` is skipped, so a rerun writes nothing twice, and a day that has not
closed or already holds promoted live rows is refused.

The static_join ingest strategy stamps each row with the static version loaded
when it is read, so before a day is read the agency's static moves forward to
the latest version named on or before that day. Static only moves forward,
never back to an older version: a backfill meant to match every day against
the timetable it ran on starts from empty static tables.

The client is read-only (ListObjectsV2 and GetObject) and signs with Signature
Version 4 from the standard library, so the deploy image needs no S3 SDK.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit

from pipeline.ingest import ingest
from pipeline.static_loader import load_static

logger = logging.getLogger(__name__)

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_ENV = {
    "endpoint": "OBJECT_STORE_ENDPOINT",
    "bucket": "OBJECT_STORE_BUCKET",
    "access_key_id": "OBJECT_STORE_ACCESS_KEY_ID",
    "secret_access_key": "OBJECT_STORE_SECRET_ACCESS_KEY",
}
_ATTEMPTS = 3
_TIMEOUT_SEC = 60


def _encode(value: str, *, keep_slash: bool = False) -> str:
    return quote(value, safe="-_.~/" if keep_slash else "-_.~")


def _canonical_query(query: str) -> str:
    # Split before decoding, so an encoded '&' or '=' inside a value stays in it.
    pairs = sorted(
        (_encode(unquote(key)), _encode(unquote(value)))
        for key, _, value in (part.partition("=") for part in query.split("&") if part)
    )
    return "&".join(f"{key}={value}" for key, value in pairs)


def sign_request(
    method: str,
    url: str,
    *,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    now: datetime,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """The headers that authenticate an S3 request with an empty body under
    Signature Version 4. The host is signed but left for the HTTP client to
    send, since it sends the one in *url*.
    """
    parts = urlsplit(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    scope = f"{now:%Y%m%d}/{region}/s3/aws4_request"
    headers = {**(extra_headers or {}), "x-amz-content-sha256": _EMPTY_SHA256, "x-amz-date": amz_date}
    signed = {"host": parts.netloc, **{name.lower(): " ".join(value.split()) for name, value in headers.items()}}
    names = sorted(signed)
    canonical_request = "\n".join(
        [
            method,
            _encode(unquote(parts.path or "/"), keep_slash=True),
            _canonical_query(parts.query),
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            _EMPTY_SHA256,
        ]
    )
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    key = f"AWS4{secret_access_key}".encode()
    for step in (f"{now:%Y%m%d}", region, "s3", "aws4_request"):
        key = hmac.new(key, step.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return headers


@dataclass(frozen=True)
class ObjectStore:
    """A read-only, path-style client for one bucket."""

    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str = field(repr=False)
    region: str = "auto"
    opener: Callable[..., Any] = field(default=urllib.request.urlopen, repr=False, compare=False)

    def __post_init__(self) -> None:
        if urlsplit(self.endpoint).scheme != "https":
            raise ValueError("OBJECT_STORE_ENDPOINT must be an https:// URL")

    @classmethod
    def from_env(cls) -> ObjectStore:
        values = {name: os.environ.get(var, "").strip() for name, var in _ENV.items()}
        missing = [var for name, var in _ENV.items() if not values[name]]
        if missing:
            raise RuntimeError(f"object store is not configured; set {', '.join(missing)}")
        return cls(
            endpoint=values["endpoint"],
            bucket=values["bucket"],
            access_key_id=values["access_key_id"],
            secret_access_key=values["secret_access_key"],
        )

    @property
    def _base(self) -> str:
        return f"{self.endpoint.rstrip('/')}/{_encode(self.bucket)}"

    def _open(self, url: str):
        headers = sign_request(
            "GET",
            url,
            access_key_id=self.access_key_id,
            secret_access_key=self.secret_access_key,
            region=self.region,
            now=datetime.now(timezone.utc),
        )
        return self.opener(urllib.request.Request(url, headers=headers), timeout=_TIMEOUT_SEC)

    def list_keys(self, prefix: str, *, start_after: str | None = None) -> list[str]:
        """Every key under *prefix*, or only those sorting after *start_after*."""
        keys: list[str] = []
        token = None
        while True:
            url = f"{self._base}?list-type=2&prefix={_encode(prefix)}"
            if start_after:
                url += f"&start-after={_encode(start_after)}"
            if token:
                url += f"&continuation-token={_encode(token)}"
            with self._open(url) as response:
                root = ET.fromstring(response.read())
            keys += [element.text or "" for element in root.iter(f"{_S3_NS}Key")]
            if (root.findtext(f"{_S3_NS}IsTruncated") or "").lower() != "true":
                return keys
            token = root.findtext(f"{_S3_NS}NextContinuationToken")
            if not token:
                raise RuntimeError(f"object store listing of {prefix} is truncated but gives no continuation token")

    def download(self, key: str, dest: Path, *, retry_wait: float = 5.0) -> None:
        """Write the object to *dest*, which appears only once it is whole.
        A network failure or a server error is retried; a 4xx is not."""
        url = f"{self._base}/{_encode(key, keep_slash=True)}"
        partial = dest.with_name(dest.name + ".part")
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                with self._open(url) as response, partial.open("wb") as out:
                    shutil.copyfileobj(response, out, 1 << 20)
                partial.replace(dest)
                return
            except (OSError, http.client.HTTPException) as exc:
                partial.unlink(missing_ok=True)
                if attempt == _ATTEMPTS or (isinstance(exc, HTTPError) and exc.code < 500):
                    raise
                logger.warning("object store: %s failed (%s); retrying", key, exc)
                time.sleep(retry_wait * attempt)


def _dated(keys: Iterable[str], pattern: str) -> list[tuple[date, str]]:
    dated = []
    for key in keys:
        match = re.fullmatch(pattern, key)
        if not match:
            continue
        try:
            dated.append((datetime.strptime(match.group(1), "%Y%m%d").date(), key))
        except ValueError:
            continue
    return sorted(dated)


def archive_keys(
    keys: Iterable[str], agency_id: int, *, since: date | None, until: date | None
) -> list[tuple[date, str]]:
    """The agency's daily tarballs among *keys* whose name day falls in
    [since, until], in day order. The name day is the collector's UTC day."""
    return [
        (day, key)
        for day, key in _dated(keys, rf"rt/{agency_id}/(\d{{8}})\.tar\.gz")
        if not (since and day < since) and not (until and day > until)
    ]


def static_versions(keys: Iterable[str], agency_id: int) -> list[tuple[date, str]]:
    """The agency's static GTFS zips among *keys*, oldest name day first."""
    return _dated(keys, rf"static/{agency_id}/gtfs_static_(\d{{8}})\.zip")


def version_for(versions: list[tuple[date, str]], day: date) -> tuple[date, str] | None:
    """The latest version named on or before *day*, or the earliest one for a
    day before them all, since that is the closest timetable there is."""
    earlier = [version for version in versions if version[0] <= day]
    return earlier[-1] if earlier else (versions[0] if versions else None)


def _loaded_static_day(conn, agency_id: int) -> date | None:
    with conn.cursor() as cur:
        cur.execute("SELECT static_version_id FROM static_trips WHERE agency_id = %s LIMIT 1", (agency_id,))
        row = cur.fetchone()
    dated = _dated([row[0]] if row and row[0] else [], r"gtfs_static_(\d{8})")
    return dated[0][0] if dated else None


def _load_static_version(store: ObjectStore, key: str, agency_id: int, conn) -> None:
    with tempfile.TemporaryDirectory(prefix="ingest_r2_") as folder:
        path = Path(folder) / Path(key).name
        store.download(key, path)
        load_static(str(path), agency_id, conn)
    logger.info("agency %s: static GTFS is now %s", agency_id, path.stem)


def ingest_from_store(
    store: ObjectStore,
    agency_id: int,
    conn,
    ch_client,
    *,
    since: date | None = None,
    until: date | None = None,
) -> Iterator[tuple[date, int]]:
    """Ingest every archived day of *agency_id* in [since, until], yielding
    each day and the rows it wrote as soon as that day is done."""
    start_after = f"rt/{agency_id}/{since:%Y%m%d}" if since else None
    days = archive_keys(
        store.list_keys(f"rt/{agency_id}/", start_after=start_after), agency_id, since=since, until=until
    )
    versions = static_versions(store.list_keys(f"static/{agency_id}/"), agency_id)
    loaded = _loaded_static_day(conn, agency_id)
    logger.info("agency %s: %d archived day(s) to read", agency_id, len(days))
    for day, key in days:
        version = version_for(versions, day)
        if version and (loaded is None or version[0] > loaded):
            _load_static_version(store, version[1], agency_id, conn)
            loaded = version[0]
        with tempfile.TemporaryDirectory(prefix="ingest_r2_") as folder:
            store.download(key, Path(folder) / Path(key).name)
            written = ingest(folder, agency_id, conn, ch_client)
        logger.info("agency %s %s: %d row(s)", agency_id, day, written)
        yield day, written
