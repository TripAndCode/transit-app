from ml.r2 import download_dir, download_file, list_keys, upload_dir, upload_file


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
