"""Thin `aws s3` wrapper for the model/forecast bucket (`transit-ml`),
matching `ml.sync`'s subprocess-injection pattern so these calls are testable
without a real network or AWS CLI. Readable without the optional `ml`
group."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

Runner = Callable[[Sequence[str]], None]


def upload_file(path: Path, bucket: str, key: str, endpoint: str, *, run: Runner) -> None:
    run(["aws", "s3", "cp", str(path), f"s3://{bucket}/{key}", "--endpoint-url", endpoint, "--only-show-errors"])


def upload_dir(directory: Path, bucket: str, prefix: str, endpoint: str, *, run: Runner) -> None:
    run(
        [
            "aws",
            "s3",
            "cp",
            str(directory),
            f"s3://{bucket}/{prefix}",
            "--recursive",
            "--endpoint-url",
            endpoint,
            "--only-show-errors",
        ]
    )
