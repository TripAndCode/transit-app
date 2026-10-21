import csv
import logging
import os

import pytest

from gtfs_pipeline import cmd_seed_agencies


class _Args:
    def __init__(self, csv_path):
        self.csv = csv_path


_HEADER = ["agency_id", "agency_name", "feed_url", "static_url", "ingest_strategy", "static_strategy"]


def _seed(tmp_path, name, rows):
    csv_path = tmp_path / name
    with csv_path.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        w.writerows(rows)
    cmd_seed_agencies(_Args(str(csv_path)))


def test_seed_agencies_populates_strategy_columns(pg_conn, tmp_path, monkeypatch):
    """Seeding from a CSV with strategy columns must persist them on agencies."""
    csv_path = tmp_path / "agencies.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "agency_id",
                "agency_name",
                "feed_url",
                "static_url",
                "ingest_strategy",
                "static_strategy",
                "trip_id_pattern",
            ]
        )
        w.writerow(
            [
                "42",
                "テスト交通",
                "http://test.example.com/feed.pb",
                "http://test.example.com/static.zip",
                "static_join",
                "direct_url",
                "",
            ]
        )

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
        w.writerow(
            [
                "agency_id",
                "agency_name",
                "feed_url",
                "static_url",
                "ingest_strategy",
                "static_strategy",
                "trip_id_pattern",
            ]
        )
        w.writerow(["43", "ブランク", "http://blank.example.com/feed.pb", "", "", "", ""])

    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    cmd_seed_agencies(_Args(str(csv_path)))

    with pg_conn.cursor() as cur:
        cur.execute("SELECT ingest_strategy, static_strategy FROM agencies WHERE agency_id = 43")
        assert cur.fetchone() == (None, None)


def test_seed_agencies_updates_feed_url_of_an_existing_agency_id(pg_conn, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    _seed(tmp_path, "a.csv", [["42", "旧URL", "http://old.example.com/feed.pb", "", "", ""]])
    _seed(tmp_path, "b.csv", [["42", "新URL", "http://new.example.com/feed.pb", "", "static_join", ""]])

    with pg_conn.cursor() as cur:
        cur.execute("SELECT agency_id, agency_name, feed_url, ingest_strategy FROM agencies ORDER BY agency_id")
        assert cur.fetchall() == [(42, "新URL", "http://new.example.com/feed.pb", "static_join")]


def test_seed_agencies_refuses_a_feed_url_held_by_another_agency_id(pg_conn, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    _seed(tmp_path, "a.csv", [["42", "既存", "http://shared.example.com/feed.pb", "", "", ""]])

    caplog.set_level(logging.ERROR, logger="gtfs_pipeline")
    with pytest.raises(SystemExit) as excinfo:
        _seed(
            tmp_path,
            "b.csv",
            [
                ["44", "先行行", "http://other.example.com/feed.pb", "", "", ""],
                ["43", "重複", "http://shared.example.com/feed.pb", "", "", ""],
            ],
        )

    assert excinfo.value.code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "agency_id 42" in errors[0]
    assert "agency_id 43" in errors[0]
    assert "http://shared.example.com/feed.pb" in errors[0]
    with pg_conn.cursor() as cur:
        cur.execute("SELECT agency_id, agency_name, feed_url FROM agencies ORDER BY agency_id")
        assert cur.fetchall() == [(42, "既存", "http://shared.example.com/feed.pb")]


def test_seed_agencies_keeps_the_sequence_past_explicit_ids(pg_conn, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    _seed(tmp_path, "a.csv", [["42", "明示", "http://explicit.example.com/feed.pb", "", "", ""]])
    _seed(tmp_path, "b.csv", [["", "自動採番", "http://implicit.example.com/feed.pb", "", "", ""]])

    with pg_conn.cursor() as cur:
        cur.execute("SELECT agency_id FROM agencies WHERE feed_url = 'http://implicit.example.com/feed.pb'")
        assert cur.fetchone() == (43,)
