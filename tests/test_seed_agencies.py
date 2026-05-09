import csv
import os

import pytest

from gtfs_pipeline import cmd_seed_agencies


class _Args:
    def __init__(self, csv_path):
        self.csv = csv_path


def test_seed_agencies_populates_strategy_columns(pg_conn, tmp_path, monkeypatch):
    """Seeding from a CSV with strategy columns must persist them on agencies."""
    csv_path = tmp_path / "agencies.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "agency_id", "agency_name", "feed_url", "static_url",
            "ingest_strategy", "static_strategy", "trip_id_pattern",
        ])
        w.writerow([
            "42", "テスト交通", "http://test.example.com/feed.pb",
            "http://test.example.com/static.zip",
            "static_join", "direct_url", "",
        ])

    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    cmd_seed_agencies(_Args(str(csv_path)))

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT agency_id, agency_name, ingest_strategy, static_strategy
            FROM agencies WHERE agency_id = 42
        """)
        row = cur.fetchone()
    assert row == (42, "テスト交通", "static_join", "direct_url")


def test_seed_agencies_blank_strategy_is_null(pg_conn, tmp_path, monkeypatch):
    csv_path = tmp_path / "agencies.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "agency_id", "agency_name", "feed_url", "static_url",
            "ingest_strategy", "static_strategy", "trip_id_pattern",
        ])
        w.writerow(["43", "ブランク", "http://blank.example.com/feed.pb", "", "", "", ""])

    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    cmd_seed_agencies(_Args(str(csv_path)))

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingest_strategy, static_strategy FROM agencies WHERE agency_id = 43"
        )
        assert cur.fetchone() == (None, None)
