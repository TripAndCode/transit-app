from pipeline.ml_forecast_ingest import fetch_latest_manifest, prune_forecast_ml_sql


def test_prune_sql_deletes_rows_for_a_service_date_in_the_past():
    sql = prune_forecast_ml_sql(14)
    assert "DELETE FROM agg_route_forecast_ml" in sql
    assert "service_date <" in sql
    assert "14 days" in sql


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects

    def list_objects_v2(self, Bucket, Prefix):
        keys = [k for k in self._objects if k.startswith(Prefix)]
        return {"Contents": [{"Key": k} for k in keys]}

    def get_object(self, Bucket, Key):
        return {"Body": _Readable(self._objects[Key])}


class _Readable:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_fetch_latest_manifest_picks_the_most_recent_date_prefix():
    manifest = b'{"rows": 2, "origin_date": "2026-10-12"}'
    s3 = _FakeS3(
        {
            "forecasts/2026-10-10/manifest.json": b'{"rows": 1, "origin_date": "2026-10-10"}',
            "forecasts/2026-10-12/manifest.json": manifest,
            "forecasts/2026-10-11/manifest.json": b'{"rows": 1, "origin_date": "2026-10-11"}',
        }
    )
    result = fetch_latest_manifest(s3, "transit-ml")
    assert result == {"rows": 2, "origin_date": "2026-10-12", "prefix": "forecasts/2026-10-12"}


def test_fetch_latest_manifest_returns_none_when_the_bucket_is_empty():
    assert fetch_latest_manifest(_FakeS3({}), "transit-ml") is None
