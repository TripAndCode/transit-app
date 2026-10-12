from ml.r2 import upload_dir, upload_file


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
