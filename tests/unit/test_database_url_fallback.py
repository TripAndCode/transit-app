"""The API and the pipeline CLI fall back to the one default the test session vets.

An empty DATABASE_URL must mean that default too. Handed to a driver as-is, an
empty string connects wherever PGHOST and PGPORT point, which the session's
dev-port guard, vetting only the default, would not see.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.db import DEFAULT_DATABASE_URL

_REPO = Path(__file__).resolve().parents[2]
_PROBE = "import json, api.main, gtfs_pipeline; print(json.dumps([api.main.DATABASE_URL, gtfs_pipeline.DATABASE_URL]))"


def _resolved_urls(database_url: str | None) -> list[str]:
    """Each module's DATABASE_URL, read in a fresh interpreter so the import-time
    fallback runs against ``database_url`` without reloading the suite's copies."""
    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "PGPORT", "PGHOST"}}
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=_REPO, env=env, capture_output=True, text=True, timeout=120, check=True
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("database_url", [None, ""], ids=["unset", "empty"])
def test_an_unset_or_empty_database_url_falls_back_to_the_default(database_url):
    assert _resolved_urls(database_url) == [DEFAULT_DATABASE_URL, DEFAULT_DATABASE_URL]


def test_a_set_database_url_is_used_as_is():
    url = "postgresql://transit:transit@localhost:5544/transit_test"

    assert _resolved_urls(url) == [url, url]
