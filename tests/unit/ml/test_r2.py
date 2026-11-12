import os
import subprocess

import pytest

from ml.r2 import (
    default_capture,
    default_run,
    download_dir,
    download_file,
    list_keys,
    upload_dir,
    upload_file,
    use_store_credentials,
)


def test_upload_file_runs_the_expected_aws_cp_command(tmp_path):
    calls = []
    source = tmp_path / "latest.json"
    source.write_text("{}")
    upload_file(source, "transit-ml", "models/latest.json", "https://example.r2", run=calls.append)
    assert calls == [
        [
            "aws",
            "s3",
            "cp",
            str(source),
            "s3://transit-ml/models/latest.json",
            "--endpoint-url",
            "https://example.r2",
            "--only-show-errors",
        ]
    ]


def test_upload_dir_runs_a_recursive_cp(tmp_path):
    calls = []
    directory = tmp_path / "model"
    directory.mkdir()
    upload_dir(directory, "transit-ml", "models/2026-10-11", "https://example.r2", run=calls.append)
    assert calls == [
        [
            "aws",
            "s3",
            "cp",
            str(directory),
            "s3://transit-ml/models/2026-10-11",
            "--recursive",
            "--endpoint-url",
            "https://example.r2",
            "--only-show-errors",
        ]
    ]


def test_list_keys_returns_each_listed_key_under_the_prefix():
    calls = []

    def capture(cmd):
        calls.append(cmd)
        return (
            "2026-11-01 11:00:03       1234 forecasts/2026-11-01/forecast.parquet\n"
            "2026-11-01 11:00:04         56 forecasts/2026-11-01/manifest.json\n"
        )

    keys = list_keys("transit-ml", "forecasts/", "https://example.r2", capture=capture)
    assert keys == ["forecasts/2026-11-01/forecast.parquet", "forecasts/2026-11-01/manifest.json"]
    assert calls == [
        ["aws", "s3", "ls", "s3://transit-ml/forecasts/", "--recursive", "--endpoint-url", "https://example.r2"]
    ]


def test_download_file_copies_the_key_to_the_destination(tmp_path):
    calls = []
    download_file(
        "transit-ml", "forecasts/2026-11-01/manifest.json", tmp_path / "m.json", "https://e", run=calls.append
    )
    assert calls == [
        [
            "aws",
            "s3",
            "cp",
            "s3://transit-ml/forecasts/2026-11-01/manifest.json",
            str(tmp_path / "m.json"),
            "--endpoint-url",
            "https://e",
            "--only-show-errors",
        ]
    ]


def test_download_dir_copies_the_prefix_recursively(tmp_path):
    calls = []
    download_dir("transit-ml", "models/2026-10-31/", tmp_path / "model", "https://e", run=calls.append)
    assert calls == [
        [
            "aws",
            "s3",
            "cp",
            "s3://transit-ml/models/2026-10-31/",
            str(tmp_path / "model"),
            "--recursive",
            "--endpoint-url",
            "https://e",
            "--only-show-errors",
        ]
    ]


def test_use_store_credentials_swaps_in_the_write_capable_pair(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-readonly-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-readonly-secret")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-write-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-write-secret")

    use_store_credentials()

    assert os.environ["AWS_ACCESS_KEY_ID"] == "store-write-key"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "store-write-secret"


def test_use_store_credentials_rejects_an_unset_but_present_variable(monkeypatch):
    # systemd's EnvironmentFile= sets an empty-but-present var for `VAR=`,
    # exactly what env.example ships as a template -- os.environ[...] would
    # return "" rather than raise, silently swapping in an empty credential.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-readonly-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-readonly-secret")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-write-secret")

    with pytest.raises(SystemExit):
        use_store_credentials()

    assert os.environ["AWS_ACCESS_KEY_ID"] == "archive-readonly-key"


def test_the_default_runner_raises_on_a_nonzero_exit():
    default_run(["true"])
    with pytest.raises(subprocess.CalledProcessError):
        default_run(["false"])


def test_the_default_capture_returns_stdout():
    assert default_capture(["printf", "listing"]) == "listing"
