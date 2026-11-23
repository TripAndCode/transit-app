"""Upload one standalone backup directory to the backup bucket.

    python scripts/upload_backup.py <directory> <stamp>

Each file in the directory goes to ``<BACKUP_STORE_PREFIX>/<stamp>/<name>``
(the prefix defaults to ``backups``) in BACKUP_STORE_BUCKET at
BACKUP_STORE_ENDPOINT, with BACKUP_STORE_ACCESS_KEY_ID and
BACKUP_STORE_SECRET_ACCESS_KEY. This key writes, so it is not the archive
bucket's read-only OBJECT_STORE_* key. scripts/standalone_backup.sh runs it in
the app image, which has boto3 and reads them from `.env`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_REQUIRED = (
    "BACKUP_STORE_ENDPOINT",
    "BACKUP_STORE_BUCKET",
    "BACKUP_STORE_ACCESS_KEY_ID",
    "BACKUP_STORE_SECRET_ACCESS_KEY",
)


def upload(client: Any, bucket: str, prefix: str, directory: Path, stamp: str) -> list[str]:
    """Upload every file in *directory* under ``<prefix>/<stamp>/``; returns the keys."""
    keys = []
    for path in sorted(p for p in directory.iterdir() if p.is_file()):
        key = f"{prefix.strip('/')}/{stamp}/{path.name}"
        client.upload_file(str(path), bucket, key)
        keys.append(key)
    if not keys:
        raise SystemExit(f"upload_backup: {directory} holds no file")
    return keys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        print("upload_backup: not set: " + ", ".join(missing), file=sys.stderr)
        return 2
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["BACKUP_STORE_ENDPOINT"],
        aws_access_key_id=os.environ["BACKUP_STORE_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["BACKUP_STORE_SECRET_ACCESS_KEY"],
    )
    bucket = os.environ["BACKUP_STORE_BUCKET"]
    prefix = os.environ.get("BACKUP_STORE_PREFIX") or "backups"
    keys = upload(client, bucket, prefix, Path(argv[0]), argv[1])
    print(f"upload_backup: {len(keys)} files to {bucket}/{prefix.strip('/')}/{argv[1]}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
