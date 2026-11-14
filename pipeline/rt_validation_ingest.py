"""Reading the daily GTFS-RT validator summaries from R2 (`transit-ml`,
``validation/<agency_id>/<YYYYMMDD>.json``, written by
scripts/rt_validate_sample.py) into ``rt_validation_daily``, one row per agency
and service date.

Every run lists the prefix and fetches only the keys whose ETag differs from
the one stored beside their row, so the growing history is not downloaded
again on each sweep, and a re-published summary replaces its row. A document
that cannot be trusted, or that names an agency the database does not hold, is
skipped by name rather than failing the batch. The counts are stored as the
validator reported them and are never graded: the shared vendor platform fires
some rules on every feed it serves.

No `ml` dependency group needed: this runs inside the production API process.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, NamedTuple

_log = logging.getLogger(__name__)

SCHEMA = 1
STATUSES = ("ok", "no_archive", "no_timetable", "validator_failed")
PREFIX = "validation/"
_KEY = re.compile(r"validation/(\d+)/(\d{8})\.json")
_RULE_FIELDS = ("rule_id", "severity", "title", "occurrences", "files_with_rule")

_UPSERT = """
    INSERT INTO rt_validation_daily
        (agency_id, service_date, status, reason, files_given, files_validated, summary, etag)
    VALUES %s
    ON CONFLICT (agency_id, service_date) DO UPDATE SET
        status = EXCLUDED.status, reason = EXCLUDED.reason, files_given = EXCLUDED.files_given,
        files_validated = EXCLUDED.files_validated, summary = EXCLUDED.summary, etag = EXCLUDED.etag,
        ingested_at = CASE WHEN rt_validation_daily.summary IS DISTINCT FROM EXCLUDED.summary
                           THEN now() ELSE rt_validation_daily.ingested_at END
    WHERE (rt_validation_daily.summary, rt_validation_daily.etag) IS DISTINCT FROM (EXCLUDED.summary, EXCLUDED.etag)
    RETURNING agency_id
"""


class ValidationReadError(Exception):
    """The prefix could not be listed, or a listed summary could not be
    fetched: a store problem, never a property of one document."""


class ValidationWriteError(Exception):
    """The summaries were read but could not be written; the batch was rolled back."""


@dataclass(frozen=True)
class ValidationIngest:
    """``fetched`` counts the keys whose ETag was new or changed, ``upserted``
    the rows inserted or changed, ``skipped`` the keys refused."""

    listed: int
    fetched: int
    upserted: int
    skipped: tuple[str, ...]


class SummaryRow(NamedTuple):
    agency_id: int
    service_date: date
    status: str
    reason: str | None
    files_given: int
    files_validated: int
    summary: str


def key_of(agency_id: int, day: date) -> str:
    return f"{PREFIX}{agency_id}/{day:%Y%m%d}.json"


def _count(value: Any, name: str, ceiling: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} {value!r} is not a count")
    if ceiling is not None and value > ceiling:
        raise ValueError(f"{name} {value} exceeds {ceiling}")
    return value


def _check_rules(rules: Any, files_validated: int) -> None:
    if not isinstance(rules, list):
        raise TypeError("rules is not a list")
    seen = set()
    for rule in rules:
        missing = [field for field in _RULE_FIELDS if field not in rule]
        if missing:
            raise KeyError(f"rule lacks {', '.join(missing)}")
        if not all(isinstance(rule[field], str) for field in ("rule_id", "severity", "title")):
            raise TypeError(f"rule {rule['rule_id']!r} has a non-text field")
        _count(rule["occurrences"], f"{rule['rule_id']} occurrences")
        _count(rule["files_with_rule"], f"{rule['rule_id']} files_with_rule", files_validated)
        if rule["rule_id"] in seen:
            raise ValueError(f"rule {rule['rule_id']} is listed twice")
        seen.add(rule["rule_id"])


def parse_summary(key: str, body: bytes) -> SummaryRow:
    """The row for one summary. Raises for a document that cannot be trusted:
    not JSON, a schema this code does not read, an agency or date other than
    the ones its key files it under, a status outside STATUSES, a reason
    stated other than exactly when the status is not ok, rule counts on a
    summary that is not ok, a count that is not one, or a non-finite number
    anywhere."""
    document = json.loads(body)
    if document.get("schema") != SCHEMA:
        raise ValueError(f"schema {document.get('schema')!r} is not {SCHEMA}")
    agency_id = document["agency_id"]
    if isinstance(agency_id, bool) or not isinstance(agency_id, int):
        raise TypeError(f"agency_id {agency_id!r} is not an id")
    service_date = date.fromisoformat(document["service_date"])
    if key != key_of(agency_id, service_date):
        raise ValueError(f"agency {agency_id} on {service_date} does not match its key")
    status, reason = document["status"], document["reason"]
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if (reason is None) != (status == "ok"):
        raise ValueError("a reason is stated exactly when the status is not ok")
    files_given = _count(document["files_given"], "files_given")
    files_validated = _count(document["files_validated"], "files_validated", files_given)
    rules = document["rules"]
    _check_rules(rules, files_validated)
    if rules and status != "ok":
        raise ValueError("only an ok summary carries rule counts")
    return SummaryRow(
        agency_id,
        service_date,
        status,
        reason,
        files_given,
        files_validated,
        json.dumps(document, allow_nan=False),
    )


def list_summaries(s3_client: Any, bucket: str) -> dict[str, str]:
    """Each key under PREFIX and its ETag, paging through the whole prefix."""
    found: dict[str, str] = {}
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": PREFIX}
        if token:
            kwargs["ContinuationToken"] = token
        response = s3_client.list_objects_v2(**kwargs)
        found.update((obj["Key"], obj["ETag"]) for obj in response.get("Contents", []))
        if not response.get("IsTruncated"):
            return found
        token = response.get("NextContinuationToken")


def _stored(conn: Any) -> tuple[dict[str, str], set[int]]:
    with conn.cursor() as cur:
        cur.execute("SELECT agency_id, service_date, etag FROM rt_validation_daily")
        etags = {key_of(agency_id, day): etag for agency_id, day, etag in cur.fetchall()}
        cur.execute("SELECT agency_id FROM agencies")
        agencies = {row[0] for row in cur.fetchall()}
    return etags, agencies


def load_validation(conn: Any, s3_client: Any, bucket: str) -> ValidationIngest:
    """Upsert every summary whose ETag is new or changed, in one transaction.
    A summary whose document is unchanged keeps its ``ingested_at``, so that
    column records when its counts last changed, which the scorecard's
    rebuild compares (pipeline.feed_quality). The caller's connection must
    not be in autocommit, or a failed batch is not undone."""
    try:
        listed = list_summaries(s3_client, bucket)
    except Exception as exc:
        raise ValidationReadError(str(exc)) from exc
    try:
        etags, agencies = _stored(conn)
        # Not left idle in a transaction across the fetches below, where its
        # locks would queue a migration's ALTER and every query behind it.
        conn.commit()
    except Exception as exc:
        raise ValidationWriteError(str(exc)) from exc

    due = sorted(key for key, etag in listed.items() if etags.get(key) != etag)
    rows: list[tuple[Any, ...]] = []
    skipped: list[str] = []
    for key in due:
        if not _KEY.fullmatch(key):
            _log.warning("rt_validation_ingest: skipped %s: not a summary key", key)
            skipped.append(key)
            continue
        try:
            body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception as exc:
            raise ValidationReadError(f"{key}: {exc}") from exc
        try:
            row = parse_summary(key, body)
        except Exception as exc:
            _log.warning("rt_validation_ingest: skipped %s: %s", key, exc)
            skipped.append(key)
            continue
        if row.agency_id not in agencies:
            _log.warning("rt_validation_ingest: skipped %s: agency %s is not in this database", key, row.agency_id)
            skipped.append(key)
            continue
        rows.append((*row, listed[key]))

    try:
        from psycopg2.extras import execute_values

        changed: list[Any] = []
        with conn.cursor() as cur:
            if rows:
                changed = execute_values(
                    cur, _UPSERT, rows, template="(%s, %s, %s, %s, %s, %s, %s::jsonb, %s)", fetch=True
                )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            _log.debug("rt_validation_ingest: rollback failed", exc_info=True)
        raise ValidationWriteError(str(exc)) from exc
    return ValidationIngest(listed=len(listed), fetched=len(due), upserted=len(changed), skipped=tuple(skipped))


def ingest_validation(conn: Any, s3_client: Any, bucket: str) -> int:
    """`load_validation` for the cron sweep: never raises, so an R2 or DB
    failure here never aborts the wider sweep."""
    try:
        return load_validation(conn, s3_client, bucket).upserted
    except ValidationReadError:
        _log.exception("rt_validation_ingest: could not read the validator summaries from R2")
    except ValidationWriteError:
        _log.exception("rt_validation_ingest: could not upsert the validator summaries")
    return 0
