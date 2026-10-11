"""Reading the ML workbench's published forecast (R2 `transit-ml` bucket) into
`agg_route_forecast_ml`. No `ml` dependency group needed -- this runs inside
the production API process, which installs `--only main`."""

from __future__ import annotations

import io
import json
import logging
from datetime import date, datetime
from typing import Any

import pyarrow.parquet as pq

_log = logging.getLogger(__name__)

FORECAST_ML_RETENTION_DAYS = 14


def _as_date(value: Any) -> date:
    # ml.cli's _predict writes origin_date as an ISO string (and so does this
    # test's parquet fixture for service_date); a real service_date column,
    # written from a pandas datetime64 column, already round-trips as a date.
    return date.fromisoformat(value) if isinstance(value, str) else value


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
    chronologically as plain strings. `None` when the bucket has no forecast
    published yet."""
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix="forecasts/")
    manifest_keys = sorted(obj["Key"] for obj in response.get("Contents", []) if obj["Key"].endswith("/manifest.json"))
    if not manifest_keys:
        return None
    key = manifest_keys[-1]
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    manifest = json.loads(body)
    manifest["prefix"] = key.rsplit("/", 1)[0]
    return manifest


async def ingest_forecast(conn: Any, s3_client: Any, bucket: str) -> int:
    """Upsert the latest published forecast into `agg_route_forecast_ml`,
    then prune rows whose service_date has aged out. Never raises: a missing
    or corrupt manifest/parquet is logged and answered with `0`, so a bad
    publish from the VPS side never aborts the caller's wider ingest sweep."""
    try:
        manifest = fetch_latest_manifest(s3_client, bucket)
        if manifest is None:
            return 0
        parquet_key = f"{manifest['prefix']}/forecast.parquet"
        body = s3_client.get_object(Bucket=bucket, Key=parquet_key)["Body"].read()
        table = pq.read_table(io.BytesIO(body))
        rows = table.to_pylist()
    except Exception:
        _log.exception("ml_forecast_ingest: could not read the latest forecast from R2")
        return 0

    for row in rows:
        await conn.execute(
            "INSERT INTO agg_route_forecast_ml "
            "(agency_id, route_code, service_date, hour, mean_min, q10, q50, q90, model_version, "
            " origin_date, generated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
            "ON CONFLICT (agency_id, route_code, service_date, hour) DO UPDATE SET "
            "mean_min = EXCLUDED.mean_min, q10 = EXCLUDED.q10, q50 = EXCLUDED.q50, q90 = EXCLUDED.q90, "
            "model_version = EXCLUDED.model_version, origin_date = EXCLUDED.origin_date, "
            "generated_at = EXCLUDED.generated_at",
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
    await conn.execute(prune_forecast_ml_sql(FORECAST_ML_RETENTION_DAYS))
    return len(rows)
