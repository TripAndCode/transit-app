"""Reading the daily GTFS-RT validator summaries from R2 (`transit-ml`,
``validation/<agency_id>/<YYYYMMDD>.json``, written by
scripts/rt_validate_sample.py) into ``rt_validation_daily``, one row per agency
and service date.

Every run lists the prefix and fetches only the keys whose ETag differs from
the one stored beside their row (or that list none), so the growing history is not downloaded
again on each sweep, and a re-published summary replaces its row. A key whose
name is not a summary's, or names an agency the database does not hold, is
skipped before it is fetched; a document that cannot be trusted is skipped
after. A skipped key stores nothing, so the next run tries it again: an agency
added later picks up its history that way. Each run logs one line counting its
skipped keys by reason. The counts are stored as the validator reported them
and are never graded: the shared vendor platform fires some rules on every
feed it serves.

No `ml` dependency group needed: this runs inside the production API process.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, NamedTuple

from pipeline.ml_forecast_ingest import list_etags

_log = logging.getLogger(__name__)

SCHEMA = 1
STATUSES = ("ok", "no_archive", "no_timetable", "validator_failed")
PREFIX = "validation/"
_KEY = re.compile(r"validation/(\d+)/(\d{8})\.json")
_RULE_FIELDS = ("rule_id", "severity", "title", "occurrences", "files_with_rule")
#: Why a key was skipped (:attr:`ValidationIngest.skipped`).
MALFORMED = "not a summary key"
UNKNOWN_AGENCY = "agency not in this database"
UNTRUSTED = "untrusted"
# The INT columns files_given and files_validated are stored in.
_MAX_COUNT = 2**31 - 1

_UPSERT = """
    INSERT INTO rt_validation_daily
        (agency_id, service_date, status, reason, files_given, files_validated, summary, etag)
    VALUES %s
    ON CONFLICT (agency_id, service_date) DO UPDATE SET
        status = EXCLUDED.status, reason = EXCLUDED.reason, files_given = EXCLUDED.files_given,
        files_validated = EXCLUDED.files_validated, summary = EXCLUDED.summary, etag = EXCLUDED.etag,
        ingested_at = CASE
            WHEN (rt_validation_daily.status, rt_validation_daily.files_validated, rt_validation_daily.summary->'rules')
                 IS DISTINCT FROM (EXCLUDED.status, EXCLUDED.files_validated, EXCLUDED.summary->'rules')
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
    """``fetched`` counts the summaries downloaded, ``upserted`` the rows
    inserted or changed, ``skipped`` the keys refused under each reason
    (MALFORMED, UNKNOWN_AGENCY, UNTRUSTED) that refused any."""

    listed: int
    fetched: int
    upserted: int
    skipped: dict[str, tuple[str, ...]]


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


def _unstorable(value: Any) -> bool:
    """Whether any text in *value*, keys included, holds U+0000, which JSONB
    refuses, or a lone surrogate (U+D800 to U+DFFF), which has no UTF-8
    encoding for psycopg2 to send."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return "\x00" in value
    if isinstance(value, dict):
        return any(_unstorable(key) or _unstorable(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_unstorable(item) for item in value)
    return False


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
    anywhere. So does a document the upsert would refuse, which would roll
    back every other summary of its batch: a reason that is not text, a file
    count past its INT column, or U+0000 or a lone surrogate anywhere."""
    document = json.loads(body)
    if _unstorable(document):
        raise ValueError("the document holds U+0000 or a lone surrogate")
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
    if reason is not None and not isinstance(reason, str):
        raise TypeError(f"reason {reason!r} is not text")
    if (reason is None) != (status == "ok"):
        raise ValueError("a reason is stated exactly when the status is not ok")
    files_given = _count(document["files_given"], "files_given", _MAX_COUNT)
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


def _stored(conn: Any) -> tuple[dict[str, str], set[int]]:
    with conn.cursor() as cur:
        cur.execute("SELECT agency_id, service_date, etag FROM rt_validation_daily")
        etags = {key_of(agency_id, day): etag for agency_id, day, etag in cur.fetchall()}
        cur.execute("SELECT agency_id FROM agencies")
        agencies = {row[0] for row in cur.fetchall()}
    return etags, agencies


def load_validation(conn: Any, s3_client: Any, bucket: str) -> ValidationIngest:
    """Upsert every summary whose ETag is new or changed, in one transaction.
    The whole document is stored, but ``ingested_at`` moves only when what
    the scorecard reads of it changes (status, polls validated, rules), since
    the scorecard's rebuild compares it (pipeline.feed_quality). The caller's connection must
    not be in autocommit, or a failed batch is not undone."""
    try:
        listed = list_etags(s3_client, bucket, PREFIX)
    except Exception as exc:
        raise ValidationReadError(str(exc)) from exc
    try:
        etags, agencies = _stored(conn)
        # Not left idle in a transaction across the fetches below, where its
        # locks would queue a migration's ALTER and every query behind it.
        conn.commit()
    except Exception as exc:
        raise ValidationWriteError(str(exc)) from exc

    # Without an ETag an unchanged key cannot be told from a re-published one.
    due = sorted(key for key, etag in listed.items() if not etag or etags.get(key) != etag)
    rows: list[tuple[Any, ...]] = []
    skipped: dict[str, list[str]] = {}

    def skip(key: str, reason: str, detail: object = "") -> None:
        _log.debug("rt_validation_ingest: skipped %s: %s %s", key, reason, detail)
        skipped.setdefault(reason, []).append(key)

    fetched = 0
    for key in due:
        match = _KEY.fullmatch(key)
        if not match:
            skip(key, MALFORMED)
            continue
        if int(match.group(1)) not in agencies:
            skip(key, UNKNOWN_AGENCY)
            continue
        try:
            body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception as exc:
            raise ValidationReadError(f"{key}: {exc}") from exc
        fetched += 1
        try:
            rows.append((*parse_summary(key, body), listed[key]))
        except Exception as exc:
            skip(key, UNTRUSTED, exc)
    if skipped:
        _log.warning(
            "rt_validation_ingest: skipped %s",
            ", ".join(f"{len(keys)} {reason}" for reason, keys in sorted(skipped.items())),
        )

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
    return ValidationIngest(
        listed=len(listed),
        fetched=fetched,
        upserted=len(changed),
        skipped={reason: tuple(keys) for reason, keys in skipped.items()},
    )


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
