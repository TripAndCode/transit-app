"""load_validation against a real database: summaries land, a key already
stored under its ETag is not fetched again, a re-published summary replaces
its row, and a summary for an agency this database does not hold is skipped
without failing the rest."""

import json
import logging
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


def test_summaries_land_and_an_unknown_agency_costs_only_the_listing(pg_conn, agency_id, caplog):
    """An agency added later picks up its history, so its keys are listed
    again on each run, but never fetched while the database lacks it."""
    unknown = rv.summary_key(agency_id + 1000, _DAY)
    store = _FakeS3({rv.summary_key(agency_id, _DAY): _summary(agency_id), unknown: _summary(agency_id + 1000)})
    with caplog.at_level(logging.WARNING):
        result = load_validation(pg_conn, store, "transit-ml")
    assert result == ValidationIngest(listed=2, fetched=1, upserted=1, skipped=(unknown,))
    assert store.fetched == [rv.summary_key(agency_id, _DAY)]
    assert [row[:6] for row in _rows(pg_conn)] == [(agency_id, _DAY, "ok", 58, "120", '"v1"')]
    # One line per run, counting the skipped keys by reason.
    (warning,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "1 agency not in this database" in warning.getMessage()

    again = _FakeS3({unknown: _summary(agency_id + 1000)})
    assert load_validation(pg_conn, again, "transit-ml").skipped == (unknown,)
    assert again.fetched == []


def test_a_poison_summary_is_skipped_without_blocking_its_neighbours(pg_conn, agency_id, caplog):
    """Each document here passes as JSON but would fail the upsert, rolling
    back the whole batch on every sweep."""
    good = rv.summary_key(agency_id, _DAY)
    reason = rv.summary_key(agency_id, date(2026, 11, 11))
    nul = rv.summary_key(agency_id, date(2026, 11, 12))
    huge = rv.summary_key(agency_id, date(2026, 11, 13))
    failed = {**_summary(agency_id), "status": "no_archive", "rules": [], "files_validated": 0}
    store = _FakeS3(
        {
            good: _summary(agency_id),
            reason: {**failed, "service_date": "2026-11-11", "reason": {"why": "gone"}},
            nul: {**failed, "service_date": "2026-11-12", "reason": "gone\x00"},
            huge: {**failed, "service_date": "2026-11-13", "reason": "gone", "files_given": 2**31},
        }
    )
    with caplog.at_level(logging.WARNING):
        result = load_validation(pg_conn, store, "transit-ml")
    assert (result.upserted, sorted(result.skipped)) == (1, sorted([reason, nul, huge]))
    assert [row[:3] for row in _rows(pg_conn)] == [(agency_id, _DAY, "ok")]
    assert "3 untrusted" in caplog.text


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


def test_a_change_the_scorecard_does_not_read_keeps_ingested_at(pg_conn, agency_id):
    """ingested_at decides whether a closed month rebuilds, so it moves only
    with what the scorecard counts: status, polls validated and rules."""
    key = rv.summary_key(agency_id, _DAY)
    load_validation(pg_conn, _FakeS3({key: _summary(agency_id)}), "transit-ml")
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE rt_validation_daily SET ingested_at = '2026-01-01T00:00:00Z'")
    pg_conn.commit()

    rerun = {**_summary(agency_id), "runtime_sec": 99.5}
    load_validation(pg_conn, _FakeS3({key: rerun}, etags={key: '"v2"'}), "transit-ml")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT summary->>'runtime_sec', ingested_at FROM rt_validation_daily")
        runtime, ingested_at = cur.fetchone()
    assert runtime == "99.5"
    assert (ingested_at.year, ingested_at.month, ingested_at.day) == (2026, 1, 1)
