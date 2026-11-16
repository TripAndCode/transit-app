"""One collector poll entering the live path, whichever collector sent it.

The Oracle collector's HTTP push (`api.routers.internal.collector_update`)
and the in-repo poller (`pipeline.poller`) both store a poll through
:func:`ingest_pushed_poll`. The poll's file name is its archive member's name
under :data:`PUSH_PREFIX`; archive replacement (`pipeline.archive_replace`)
reads that prefix as "the collector pushed this poll" and checks every such
poll against the day's tarballs by member name, so a poll stored any other
way would escape that check.

Kept free of heavy imports: the API router imports it at module load.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

PUSH_PREFIX = "oracle/"
SOURCE_FILE_RE = re.compile(r"^[0-9]{8}/TripUpdate_[0-9]{6}\.pb$")
# The largest poll either collector stores: the endpoint refuses a bigger
# body, and the poller stops reading a feed past it.
MAX_POLL_BYTES = 10 * 1024 * 1024


class UnknownAgency(ValueError):
    """A push for an agency that does not exist or is disabled."""


def member_name(at: datetime) -> str:
    """The archive member a poll taken at *at* is filed under:
    ``<UTC YYYYMMDD>/TripUpdate_<UTC HHMMSS>.pb``, as `rt-poller.sh` names it.
    Day and time come from one instant, so a poll at midnight is never named
    for one day and filed in the other."""
    utc = at.astimezone(timezone.utc)
    return f"{utc:%Y%m%d}/TripUpdate_{utc:%H%M%S}.pb"


def require_agency(conn, agency_id: int) -> None:
    """Raise :class:`UnknownAgency` unless *agency_id* exists and is enabled."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM agencies WHERE agency_id = %s AND deleted_at IS NULL",
            (agency_id,),
        )
        if cur.fetchone() is None:
            raise UnknownAgency(f"Unknown or deleted agency_id={agency_id}")


def ingest_pushed_poll(agency_id: int, raw: bytes, captured_at: str, source_file: str, conn, ch_client) -> int:
    """Store one pushed poll in `updates_live` and return the rows inserted.

    *source_file* is the poll's archive member name. Raises ``ValueError``
    for a name not in the collector's format, :class:`UnknownAgency` for an
    agency that is missing or disabled, and whatever
    `pipeline.ingest.ingest_live_payload` raises.
    """
    if not SOURCE_FILE_RE.fullmatch(source_file):
        raise ValueError(f"not a collector member name: {source_file!r}")
    from pipeline import ingest

    require_agency(conn, agency_id)
    return ingest.ingest_live_payload(agency_id, raw, captured_at, f"{PUSH_PREFIX}{source_file}", conn, ch_client)
