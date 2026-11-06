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


Capture = Callable[[Sequence[str]], str]


def list_keys(bucket: str, prefix: str, endpoint: str, *, capture: Capture) -> list[str]:
    """Every key under *prefix*: `aws s3 ls --recursive` prints date, time, size and key."""
    output = capture(["aws", "s3", "ls", f"s3://{bucket}/{prefix}", "--recursive", "--endpoint-url", endpoint])
    return [parts[3] for line in output.splitlines() if len(parts := line.split(None, 3)) == 4]


def download_file(bucket: str, key: str, dest: Path, endpoint: str, *, run: Runner) -> None:
    run(["aws", "s3", "cp", f"s3://{bucket}/{key}", str(dest), "--endpoint-url", endpoint, "--only-show-errors"])


def download_dir(bucket: str, prefix: str, dest: Path, endpoint: str, *, run: Runner) -> None:
    run(
        [
            "aws",
            "s3",
            "cp",
            f"s3://{bucket}/{prefix}",
            str(dest),
            "--recursive",
            "--endpoint-url",
            endpoint,
            "--only-show-errors",
        ]
    )
