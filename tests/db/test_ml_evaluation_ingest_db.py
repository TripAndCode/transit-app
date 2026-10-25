"""load_evaluations against a real database: rows land, a re-published week
replaces its row, and the stored document round-trips."""

import json
from datetime import date

from pipeline.ml_evaluation_ingest import EvaluationIngest, load_evaluations


class _FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)]}

    def get_object(self, Bucket, Key):
        data = self.objects[Key]

        class _Body:
            def read(self):
                return data

        return {"Body": _Body()}


def _store(**weeks):
    return _FakeS3(
        {
            f"evaluations/{run}/summary.json": json.dumps(
                {"schema": 1, "run_date": run, "adopted": adopted, "headline": {"skill": skill, "coverage": 0.8}}
            ).encode()
            for run, (adopted, skill) in weeks.items()
        }
    )


def test_every_week_lands_and_a_republished_week_replaces_its_row(pg_conn):
    first = _store(**{"2026-10-18": (True, 0.063), "2026-10-25": (False, 0.046)})
    assert load_evaluations(pg_conn, first, "transit-ml") == EvaluationIngest(listed=2, upserted=2, skipped=())

    republished = _store(**{"2026-10-18": (True, 0.063), "2026-10-25": (True, 0.051)})
    load_evaluations(pg_conn, republished, "transit-ml")

    with pg_conn.cursor() as cur:
        cur.execute("SELECT run_date, adopted, overall_skill, summary FROM ml_evaluations ORDER BY run_date")
        rows = cur.fetchall()
    assert [(r[0], r[1], round(r[2], 3)) for r in rows] == [
        (date(2026, 10, 18), True, 0.063),
        (date(2026, 10, 25), True, 0.051),
    ]
    assert rows[1][3]["headline"]["skill"] == 0.051


def test_an_unchanged_week_is_left_alone_so_ingested_at_means_when_it_last_changed(pg_conn):
    store = _store(**{"2026-10-18": (True, 0.063)})
    load_evaluations(pg_conn, store, "transit-ml")
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE ml_evaluations SET ingested_at = '2026-01-01T00:00:00Z'")
    pg_conn.commit()

    assert load_evaluations(pg_conn, store, "transit-ml").upserted == 0
    with pg_conn.cursor() as cur:
        cur.execute("SELECT ingested_at::date FROM ml_evaluations")
        assert cur.fetchone()[0] == date(2026, 1, 1)

    load_evaluations(pg_conn, _store(**{"2026-10-18": (True, 0.064)}), "transit-ml")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT ingested_at::date FROM ml_evaluations")
        assert cur.fetchone()[0] != date(2026, 1, 1)
