"""Reading the ML workbench's weekly documents from R2 (`transit-ml`): each
evaluation summary (`evaluations/<run_date>/summary.json`) into `ml_evaluations`,
and each live scorecard (`evaluations/<run_date>/live.json`) into
`ml_live_evaluations`. Every published week is upserted on each run, so a
re-published week replaces its row and a missed run catches up. No `ml`
dependency group needed: this runs inside the production API process."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pipeline.ml_forecast_ingest import list_keys

_log = logging.getLogger(__name__)

SUPPORTED_SCHEMA = 1
LIVE_SCHEMA = 1
_PREFIX = "evaluations/"
_SUFFIX = "/summary.json"
_LIVE_SUFFIX = "/live.json"
_SUMMARY_UPSERT = (
    "INSERT INTO ml_evaluations (run_date, schema_version, adopted, overall_skill, summary) "
    "VALUES %s ON CONFLICT (run_date) DO UPDATE SET schema_version = EXCLUDED.schema_version, "
    "adopted = EXCLUDED.adopted, overall_skill = EXCLUDED.overall_skill, "
    "summary = EXCLUDED.summary, ingested_at = now() "
    "WHERE ml_evaluations.summary IS DISTINCT FROM EXCLUDED.summary RETURNING run_date"
)
_LIVE_UPSERT = (
    "INSERT INTO ml_live_evaluations (run_date, schema_version, document) "
    "VALUES %s ON CONFLICT (run_date) DO UPDATE SET schema_version = EXCLUDED.schema_version, "
    "document = EXCLUDED.document, ingested_at = now() "
    "WHERE ml_live_evaluations.document IS DISTINCT FROM EXCLUDED.document RETURNING run_date"
)


class EvaluationReadError(Exception):
    """The evaluations prefix could not be listed, or a listed document could
    not be fetched: a store problem, never a property of one document."""


class EvaluationWriteError(Exception):
    """The documents were read but could not be written; the batch was rolled back."""


@dataclass(frozen=True)
class EvaluationIngest:
    """`upserted` counts the weeks inserted or changed; an unchanged week is
    listed but not upserted."""

    listed: int
    upserted: int
    skipped: tuple[str, ...]


def _document(key: str, body: bytes, suffix: str, schema: int) -> tuple[date, dict[str, Any], str]:
    """The run date, the parsed document and its JSON for the row. Raises for a
    document that cannot be trusted: not JSON, a schema this code does not read,
    a run date other than the one its key files it under, or a non-finite number
    anywhere, which would otherwise fail the whole batch's write."""
    document = json.loads(body)
    if document.get("schema") != schema:
        raise ValueError(f"schema {document.get('schema')!r} is not {schema}")
    run_date = date.fromisoformat(document["run_date"])
    if key != f"{_PREFIX}{run_date.isoformat()}{suffix}":
        raise ValueError(f"run_date {run_date} does not match its key")
    return run_date, document, json.dumps(document, allow_nan=False)


def _row(key: str, body: bytes) -> tuple[Any, ...]:
    """The ml_evaluations row for one summary. Beyond `_document`'s checks, a
    missing field or a skill that is not a number is untrustworthy too."""
    run_date, summary, document = _document(key, body, _SUFFIX, SUPPORTED_SCHEMA)
    skill = summary["headline"]["skill"]
    if skill is not None and (isinstance(skill, bool) or not isinstance(skill, int | float)):
        raise ValueError(f"headline skill {skill!r} is not a number")
    return (run_date, SUPPORTED_SCHEMA, bool(summary["adopted"]), skill, document)


def _live_row(key: str, body: bytes) -> tuple[Any, ...]:
    run_date, _, document = _document(key, body, _LIVE_SUFFIX, LIVE_SCHEMA)
    return (run_date, LIVE_SCHEMA, document)


def load_evaluations(conn: Any, s3_client: Any, bucket: str) -> EvaluationIngest:
    """Upsert every published summary, in one transaction; a week whose
    document is unchanged is left as it is, so `ingested_at` records when its
    row last changed. The caller's connection must not be in autocommit, or a
    failed batch is not undone."""
    return _load(conn, s3_client, bucket, _SUFFIX, _row, _SUMMARY_UPSERT, "(%s, %s, %s, %s, %s::jsonb)")


def load_live_evaluations(conn: Any, s3_client: Any, bucket: str) -> EvaluationIngest:
    """`load_evaluations` for the live scorecards, into ml_live_evaluations."""
    return _load(conn, s3_client, bucket, _LIVE_SUFFIX, _live_row, _LIVE_UPSERT, "(%s, %s, %s::jsonb)")


def _load(
    conn: Any,
    s3_client: Any,
    bucket: str,
    suffix: str,
    row: Callable[[str, bytes], tuple[Any, ...]],
    upsert: str,
    template: str,
) -> EvaluationIngest:
    try:
        keys = list_keys(s3_client, bucket, _PREFIX, suffix)
    except Exception as exc:
        raise EvaluationReadError(str(exc)) from exc

    rows: list[tuple[Any, ...]] = []
    skipped: list[str] = []
    for key in keys:
        try:
            body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception as exc:
            raise EvaluationReadError(f"{key}: {exc}") from exc
        try:
            rows.append(row(key, body))
        except Exception as exc:
            _log.warning("ml_evaluation_ingest: skipped %s: %s", key, exc)
            skipped.append(key)

    try:
        from psycopg2.extras import execute_values

        changed: list[Any] = []
        with conn.cursor() as cur:
            if rows:
                changed = execute_values(cur, upsert, rows, template=template, fetch=True)
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            _log.debug("ml_evaluation_ingest: rollback failed", exc_info=True)
        raise EvaluationWriteError(str(exc)) from exc
    return EvaluationIngest(listed=len(keys), upserted=len(changed), skipped=tuple(skipped))


def ingest_evaluations(conn: Any, s3_client: Any, bucket: str) -> int:
    """`load_evaluations` for the cron sweep: never raises, so an R2 or DB
    failure here never aborts the wider ingest sweep."""
    return _ingest(load_evaluations, conn, s3_client, bucket, "evaluation summaries")


def ingest_live_evaluations(conn: Any, s3_client: Any, bucket: str) -> int:
    """`load_live_evaluations` for the cron sweep, which never raises either."""
    return _ingest(load_live_evaluations, conn, s3_client, bucket, "live scorecards")


def _ingest(
    load: Callable[[Any, Any, str], EvaluationIngest], conn: Any, s3_client: Any, bucket: str, what: str
) -> int:
    try:
        return load(conn, s3_client, bucket).upserted
    except EvaluationReadError:
        _log.exception("ml_evaluation_ingest: could not read the %s from R2", what)
    except EvaluationWriteError:
        _log.exception("ml_evaluation_ingest: could not upsert the %s", what)
    return 0
