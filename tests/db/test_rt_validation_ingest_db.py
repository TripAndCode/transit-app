"""load_validation against a real database: summaries land, a key already
stored under its ETag is not fetched again, a re-published summary replaces
its row, and a summary for an agency this database does not hold is skipped
without failing the rest."""

import json
from datetime import date

from pipeline.rt_validation_ingest import ValidationIngest, load_validation
from scripts import rt_validate_sample as rv

_DAY = date(2026, 11, 10)


def _summary(agency_id, day=_DAY, occurrences=120):
    return rv.summary(
        agency_id=agency_id,
        service_date=day,
        validator=rv.validator_identity("docker"),
        status="ok",
        files_given=60,
        files_validated=58,
        rules=[
            {"rule_id": "E022", "severity": "ERROR", "title": "t", "occurrences": occurrences, "files_with_rule": 58}
        ],
    )


class _FakeS3:
    def __init__(self, documents: dict[str, dict], etags: dict[str, str] | None = None):
        self.objects = {key: json.dumps(doc).encode() for key, doc in documents.items()}
        self.etags = etags or {}
        self.fetched: list[str] = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        return {
            "Contents": [
                {"Key": key, "ETag": self.etags.get(key, '"v1"')} for key in self.objects if key.startswith(Prefix)
            ]
        }

    def get_object(self, Bucket, Key):
        self.fetched.append(Key)
        data = self.objects[Key]

        class _Body:
            def read(self):
                return data

        return {"Body": _Body()}


def _rows(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id, service_date, status, files_validated, summary->'rules'->0->>'occurrences', etag, "
            "ingested_at FROM rt_validation_daily ORDER BY agency_id, service_date"
        )
        return cur.fetchall()


def test_summaries_land_and_an_unknown_agency_is_skipped_alone(pg_conn, agency_id):
    store = _FakeS3(
        {
            rv.summary_key(agency_id, _DAY): _summary(agency_id),
            rv.summary_key(agency_id + 1000, _DAY): _summary(agency_id + 1000),
        }
    )
    result = load_validation(pg_conn, store, "transit-ml")
    assert result == ValidationIngest(
        listed=2, fetched=2, upserted=1, skipped=(rv.summary_key(agency_id + 1000, _DAY),)
    )
    rows = _rows(pg_conn)
    assert [row[:6] for row in rows] == [(agency_id, _DAY, "ok", 58, "120", '"v1"')]


def test_a_key_stored_under_its_etag_is_not_fetched_again(pg_conn, agency_id):
    key = rv.summary_key(agency_id, _DAY)
    load_validation(pg_conn, _FakeS3({key: _summary(agency_id)}), "transit-ml")
    again = _FakeS3({key: _summary(agency_id)})
    assert load_validation(pg_conn, again, "transit-ml") == ValidationIngest(
        listed=1, fetched=0, upserted=0, skipped=()
    )
    assert again.fetched == []


def test_a_republished_summary_replaces_its_row_and_moves_ingested_at(pg_conn, agency_id):
    key = rv.summary_key(agency_id, _DAY)
    load_validation(pg_conn, _FakeS3({key: _summary(agency_id)}), "transit-ml")
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE rt_validation_daily SET ingested_at = '2026-01-01T00:00:00Z'")
    pg_conn.commit()

    republished = _FakeS3({key: _summary(agency_id, occurrences=7)}, etags={key: '"v2"'})
    assert load_validation(pg_conn, republished, "transit-ml").upserted == 1
    (row,) = _rows(pg_conn)
    assert row[4:6] == ("7", '"v2"')
    assert row[6].year == 2026 and row[6].month != 1


def test_a_new_etag_over_the_same_document_keeps_ingested_at(pg_conn, agency_id):
    key = rv.summary_key(agency_id, _DAY)
    load_validation(pg_conn, _FakeS3({key: _summary(agency_id)}), "transit-ml")
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE rt_validation_daily SET ingested_at = '2026-01-01T00:00:00Z'")
    pg_conn.commit()

    load_validation(pg_conn, _FakeS3({key: _summary(agency_id)}, etags={key: '"v2"'}), "transit-ml")
    (row,) = _rows(pg_conn)
    assert row[5] == '"v2"'
    assert (row[6].year, row[6].month, row[6].day) == (2026, 1, 1)
