"""Thin `aws s3` wrapper for the model/forecast bucket (`transit-ml`),
matching `ml.sync`'s subprocess-injection pattern so these calls are testable
without a real network or AWS CLI. Readable without the optional `ml`
group."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

Runner = Callable[[Sequence[str]], None]


def default_run(cmd: Sequence[str]) -> None:
    """The Runner every job passes for real: a nonzero exit raises CalledProcessError."""
    subprocess.run(list(cmd), check=True)


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


def default_capture(cmd: Sequence[str]) -> str:
    """The Capture every job passes for real: stdout, and a nonzero exit raises CalledProcessError."""
    return subprocess.run(list(cmd), check=True, capture_output=True, text=True).stdout


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


def use_store_credentials() -> None:
    """Swap the transit-ml read-write pair into AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY
    for the rest of this process.

    `sync` reads this same EnvironmentFile and must keep using the read-only
    transit-archives pair in those variables, so each job that reads or writes
    the transit-ml bucket swaps in its read-write pair only for its own
    (separate, short-lived) process, from distinctly named variables. A job
    that also reads transit-archives swaps only after its last read there: the
    read-write pair is scoped to transit-ml alone.
    """
    access_key, secret_key = os.environ["ML_STORE_ACCESS_KEY_ID"], os.environ["ML_STORE_SECRET_ACCESS_KEY"]
    # systemd's EnvironmentFile= sets an empty-but-present var for `VAR=` --
    # env.example ships exactly that, so a `KeyError` alone would not catch
    # an operator who never filled it in.
    if not access_key or not secret_key:
        raise SystemExit("ML_STORE_ACCESS_KEY_ID/ML_STORE_SECRET_ACCESS_KEY must be set, not empty")
    os.environ["AWS_ACCESS_KEY_ID"] = access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
