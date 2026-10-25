"""Reading the ML workbench's weekly evaluation summaries (R2 `transit-ml`,
`evaluations/<run_date>/summary.json`) into `ml_evaluations`. Every published
week is upserted on each run, so a re-published week replaces its row and a
missed run catches up. No `ml` dependency group needed: this runs inside the
production API process."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from pipeline.ml_forecast_ingest import list_keys

_log = logging.getLogger(__name__)

SUPPORTED_SCHEMA = 1
_PREFIX = "evaluations/"
_SUFFIX = "/summary.json"


class EvaluationReadError(Exception):
    """The evaluations prefix could not be listed."""


class EvaluationWriteError(Exception):
    """The summaries were read but could not be written; the batch was rolled back."""


@dataclass(frozen=True)
class EvaluationIngest:
    listed: int
    upserted: int
    skipped: tuple[str, ...]


def _row(key: str, body: bytes) -> tuple[Any, ...]:
    """The table row for one summary. Raises for a document that cannot be
    trusted: not JSON, a schema this code does not read, a missing field, or a
    run date other than the one its key files it under."""
    summary = json.loads(body)
    if summary.get("schema") != SUPPORTED_SCHEMA:
        raise ValueError(f"schema {summary.get('schema')!r} is not {SUPPORTED_SCHEMA}")
    run_date = date.fromisoformat(summary["run_date"])
    if key != f"{_PREFIX}{run_date.isoformat()}{_SUFFIX}":
        raise ValueError(f"run_date {run_date} does not match its key")
    return (run_date, SUPPORTED_SCHEMA, bool(summary["adopted"]), summary["headline"]["skill"], json.dumps(summary))


def load_evaluations(conn: Any, s3_client: Any, bucket: str) -> EvaluationIngest:
    """Upsert every published summary, in one transaction. The caller's
    connection must not be in autocommit, or a failed batch is not undone."""
    try:
        keys = list_keys(s3_client, bucket, _PREFIX, _SUFFIX)
    except Exception as exc:
        raise EvaluationReadError(str(exc)) from exc

    rows: list[tuple[Any, ...]] = []
    skipped: list[str] = []
    for key in keys:
        try:
            body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
            rows.append(_row(key, body))
        except Exception as exc:
            _log.warning("ml_evaluation_ingest: skipped %s: %s", key, exc)
            skipped.append(key)

    try:
        from psycopg2.extras import execute_values

        with conn.cursor() as cur:
            if rows:
                execute_values(
                    cur,
                    "INSERT INTO ml_evaluations (run_date, schema_version, adopted, overall_skill, summary) "
                    "VALUES %s ON CONFLICT (run_date) DO UPDATE SET schema_version = EXCLUDED.schema_version, "
                    "adopted = EXCLUDED.adopted, overall_skill = EXCLUDED.overall_skill, "
                    "summary = EXCLUDED.summary, ingested_at = now()",
                    rows,
                    template="(%s, %s, %s, %s, %s::jsonb)",
                )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            _log.debug("ml_evaluation_ingest: rollback failed", exc_info=True)
        raise EvaluationWriteError(str(exc)) from exc
    return EvaluationIngest(listed=len(keys), upserted=len(rows), skipped=tuple(skipped))


def ingest_evaluations(conn: Any, s3_client: Any, bucket: str) -> int:
    """`load_evaluations` for the cron sweep: never raises, so an R2 or DB
    failure here never aborts the wider ingest sweep."""
    try:
        return load_evaluations(conn, s3_client, bucket).upserted
    except EvaluationReadError:
        _log.exception("ml_evaluation_ingest: could not list the evaluation summaries")
    except EvaluationWriteError:
        _log.exception("ml_evaluation_ingest: could not upsert the evaluation summaries")
    return 0
