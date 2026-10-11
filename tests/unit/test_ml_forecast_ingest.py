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


class _PagedFakeS3:
    """Splits `pages` across successive list_objects_v2 calls, the way a
    real S3-compatible bucket paginates once a prefix holds more keys than
    one page -- fetch_latest_manifest must follow IsTruncated/
    NextContinuationToken rather than trusting a single response."""

    def __init__(self, pages: list[list[str]], objects: dict[str, bytes]):
        self._pages = pages
        self._objects = objects

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        index = int(ContinuationToken) if ContinuationToken else 0
        page = self._pages[index]
        truncated = index + 1 < len(self._pages)
        return {
            "Contents": [{"Key": k} for k in page],
            "IsTruncated": truncated,
            "NextContinuationToken": str(index + 1) if truncated else None,
        }

    def get_object(self, Bucket, Key):
        return {"Body": _Readable(self._objects[Key])}


def test_fetch_latest_manifest_follows_pagination_across_multiple_pages():
    # The newest manifest sorts last and sits on the second (later) page --
    # a caller that reads only the first page would miss it.
    manifest = b'{"rows": 1, "origin_date": "2026-10-12"}'
    objects = {
        "forecasts/2026-10-01/manifest.json": b'{"rows": 1, "origin_date": "2026-10-01"}',
        "forecasts/2026-10-12/manifest.json": manifest,
    }
    s3 = _PagedFakeS3(
        pages=[
            ["forecasts/2026-10-01/manifest.json"],
            ["forecasts/2026-10-12/manifest.json"],
        ],
        objects=objects,
    )
    result = fetch_latest_manifest(s3, "transit-ml")
    assert result == {"rows": 1, "origin_date": "2026-10-12", "prefix": "forecasts/2026-10-12"}
