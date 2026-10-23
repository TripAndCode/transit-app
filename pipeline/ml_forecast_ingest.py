"""Reading the ML workbench's published forecast (R2 `transit-ml` bucket) into
`agg_route_forecast_ml`. No `ml` dependency group needed -- this runs inside
the production API process, which installs `--only main`."""

from __future__ import annotations

import io
import json
import logging
from datetime import date, datetime
from typing import Any

_log = logging.getLogger(__name__)

FORECAST_ML_RETENTION_DAYS = 14

_COLUMNS = (
    "agency_id",
    "route_code",
    "service_date",
    "hour",
    "mean_min",
    "q10",
    "q50",
    "q90",
    "model_version",
    "origin_date",
    "generated_at",
)


def _as_date(value: Any) -> date:
    # A real service_date column, written from a pandas datetime64 column,
    # round-trips through parquet as a native datetime, not a date; origin_date
    # (ml.cli's _predict writes it as an ISO string) round-trips as a str.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _as_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def prune_forecast_ml_sql(days: int) -> str:
    """DELETE text for rows whose service_date has aged past the forecast's
    own usefulness window. Not part of `pipeline.retention.policy_prune_sql`:
    that aggregator is reserved for privacy-policy-mandated windows; this one
    is a plain freshness cleanup, run from the same ingest step that writes
    this table (see `ingest_forecast`), not the daily privacy-prune pass."""
    return f"DELETE FROM agg_route_forecast_ml WHERE service_date < now()::date - INTERVAL '{int(days)} days'"


def fetch_latest_manifest(s3_client: Any, bucket: str) -> dict[str, Any] | None:
    """The most recent `forecasts/<date>/manifest.json`, by its own `prefix`
    (`forecasts/<date>`) sorted lexicographically -- ISO dates sort
    chronologically as plain strings. Pages through the whole `forecasts/`
    prefix: a single unpaginated `list_objects_v2` call silently truncates
    past ~500 days of daily publishing (1000 keys / 2 objects a day), which
    would make this quietly stop seeing newer manifests. `None` when the
    bucket has no forecast published yet."""
    manifest_keys: list[str] = []
    continuation_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": "forecasts/"}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = s3_client.list_objects_v2(**kwargs)
        manifest_keys.extend(
            obj["Key"] for obj in response.get("Contents", []) if obj["Key"].endswith("/manifest.json")
        )
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")
    if not manifest_keys:
        return None
    key = sorted(manifest_keys)[-1]
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    manifest = json.loads(body)
    manifest["prefix"] = key.rsplit("/", 1)[0]
    return manifest


def ingest_forecast(conn: Any, s3_client: Any, bucket: str) -> int:
    """Upsert the latest published forecast into `agg_route_forecast_ml`,
    then prune rows whose service_date has aged out, all in one transaction
    (a failure partway through rolls back the whole batch rather than
    leaving a half-upserted forecast with the prune skipped). Never raises:
    a missing/corrupt manifest, a forecast whose rows lack a column or carry
    a non-ISO date, or a DB failure is logged and answered with `0`, so a bad
    publish from the VPS side never aborts the caller's wider ingest sweep."""
    import pyarrow.parquet as pq

    try:
        manifest = fetch_latest_manifest(s3_client, bucket)
        if manifest is None:
            return 0
        parquet_key = f"{manifest['prefix']}/forecast.parquet"
        body = s3_client.get_object(Bucket=bucket, Key=parquet_key)["Body"].read()
        table = pq.read_table(io.BytesIO(body))
        pg_rows = [
            (
                row["agency_id"],
                row["route_code"],
                _as_date(row["service_date"]),
                row["hour"],
                row["mean_min"],
                row["q10"],
                row["q50"],
                row["q90"],
                row["model_version"],
                _as_date(row["origin_date"]),
                _as_datetime(row["generated_at"]),
            )
            for row in table.to_pylist()
        ]
    except Exception:
        _log.exception("ml_forecast_ingest: could not read the latest forecast from R2")
        return 0

    try:
        from psycopg2.extras import execute_values

        with conn.cursor() as cur:
            if pg_rows:
                execute_values(
                    cur,
                    f"INSERT INTO agg_route_forecast_ml ({', '.join(_COLUMNS)}) VALUES %s "
                    "ON CONFLICT (agency_id, route_code, service_date, hour) DO UPDATE SET "
                    "mean_min = EXCLUDED.mean_min, q10 = EXCLUDED.q10, q50 = EXCLUDED.q50, q90 = EXCLUDED.q90, "
                    "model_version = EXCLUDED.model_version, origin_date = EXCLUDED.origin_date, "
                    "generated_at = EXCLUDED.generated_at",
                    pg_rows,
                )
            cur.execute(prune_forecast_ml_sql(FORECAST_ML_RETENTION_DAYS))
        conn.commit()
    except Exception:
        conn.rollback()
        _log.exception("ml_forecast_ingest: could not upsert the latest forecast")
        return 0
    return len(pg_rows)
