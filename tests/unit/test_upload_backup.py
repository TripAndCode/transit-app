from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("upload_backup", ROOT / "scripts" / "upload_backup.py")
upload_backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(upload_backup)


class _Client:
    def __init__(self):
        self.sent = []

    def upload_file(self, filename, bucket, key):
        self.sent.append((Path(filename).name, bucket, key))


def test_each_file_goes_under_the_prefix_and_the_stamp(tmp_path):
    (tmp_path / "postgres.dump").write_bytes(b"x")
    (tmp_path / "clickhouse.zip").write_bytes(b"y")
    client = _Client()
    keys = upload_backup.upload(client, "bucket", "/backups/", tmp_path, "20260101T000000Z")
    assert keys == ["backups/20260101T000000Z/clickhouse.zip", "backups/20260101T000000Z/postgres.dump"]
    assert client.sent == [
        ("clickhouse.zip", "bucket", "backups/20260101T000000Z/clickhouse.zip"),
        ("postgres.dump", "bucket", "backups/20260101T000000Z/postgres.dump"),
    ]


def test_an_empty_directory_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        upload_backup.upload(_Client(), "bucket", "backups", tmp_path, "s")


def test_missing_settings_are_named_and_no_value_is_printed(tmp_path, monkeypatch, capsys):
    for name in upload_backup._REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BACKUP_STORE_SECRET_ACCESS_KEY", "s3cr3t-value")
    assert upload_backup.main([str(tmp_path), "s"]) == 2
    err = capsys.readouterr().err
    assert "BACKUP_STORE_BUCKET" in err
    assert "BACKUP_STORE_SECRET_ACCESS_KEY" not in err
    assert "s3cr3t-value" not in err
