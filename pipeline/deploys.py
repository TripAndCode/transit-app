"""The release record: which commit each deployment brought up, when, and
what it migrated.

Every deployment runs `gtfs_pipeline.py schema-up` before the new code
starts: the standalone stack's `migrate` service and Railway's
preDeployCommand. That step is where a release records itself, so no
promotion depends on someone remembering to tag it. The admin ops page reads
the newest row. Nothing stops two concurrent runs for one release from each
adding a row; a deployment runs the step once, so that is left unguarded.

A restart runs the step again. A run that applies no migration for the same
commit as the newest row is the same release, so it adds no row, and
`deployed_at` stays the time that release's schema step first completed.

Bookkeeping never breaks the deploy: by the time the row is written both
schemas are up, and a failed insert costs the release its row and nothing
else.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

import asyncpg

logger = logging.getLogger(__name__)

#: Where the commit comes from, the first one set winning: the Dockerfile's
#: `GIT_SHA` build argument, baked into the image, then the variable Railway
#: sets on a deploy from GitHub.
SHA_ENV_VARS: tuple[str, ...] = ("GIT_SHA", "RAILWAY_GIT_COMMIT_SHA")

#: Mirrors the `git_sha` CHECK constraint.
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")

_RECORD_SQL = """
INSERT INTO deploys (git_sha, migrations_applied)
SELECT %(sha)s::text, %(applied)s::text[]
WHERE cardinality(%(applied)s::text[]) > 0
   OR %(sha)s::text IS DISTINCT FROM (SELECT git_sha FROM deploys ORDER BY deploy_id DESC LIMIT 1)
"""


def release_sha(environ: Mapping[str, str] = os.environ) -> str | None:
    """The running image's commit, or None when it was built without one.

    A value that is not a commit hash is treated as missing rather than
    passed on: the row is worth more without a commit than not at all, and
    the CHECK would refuse it.
    """
    for name in SHA_ENV_VARS:
        value = environ.get(name, "").strip().lower()
        if not value:
            continue
        if _SHA_RE.fullmatch(value):
            return value
        logger.warning("%s is not a commit hash; the release is recorded without one", name)
        return None
    return None


def record_deploy(connect: Callable[[], Any], sha: str | None, applied: Sequence[str]) -> None:
    """Record the release unless it is the newest row's, rerun.

    *connect* opens a psycopg2 connection; this commits and closes its own.
    """
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_RECORD_SQL, {"sha": sha, "applied": list(applied)})
                recorded = cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("schema-up: the release was not recorded", exc_info=True)
        return
    if recorded:
        logger.info("schema-up: recorded release %s (%d migration(s))", sha or "of unknown commit", len(applied))
    else:
        logger.info("schema-up: release %s already recorded", sha or "of unknown commit")


@dataclass(frozen=True)
class Deploy:
    deployed_at: datetime
    git_sha: str | None
    migrations_applied: list[str]


async def latest_deploy(conn: asyncpg.Connection) -> Deploy | None:
    """The newest recorded release, or None when none has been recorded."""
    row = await conn.fetchrow(
        "SELECT deployed_at, git_sha, migrations_applied FROM deploys ORDER BY deploy_id DESC LIMIT 1"
    )
    if row is None:
        return None
    return Deploy(row["deployed_at"], row["git_sha"], list(row["migrations_applied"]))
