"""The throwaway `make analyze-all` command AGENTS.md documents really reaches
the throwaway stores, even when `.env` points at the dev ones.

The Makefile's `-include .env` outranks the environment, so an env prefix
before `make` is silently replaced by `.env`'s value; only a VAR=value argument
after `make` wins. The documented command is run, with its target swapped for a
probe that prints what a recipe would see, against a copy of the Makefile in
`tmp_path` whose `.env` names dev values -- no real `.env` is read and no store
is touched.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
POINTED = ("DATABASE_URL", "CLICKHOUSE_PORT", "CLICKHOUSE_DATABASE")
DEV_ENV = "DATABASE_URL=postgresql://dev:dev@devhost:5433/transit\nCLICKHOUSE_PORT=8123\nCLICKHOUSE_DATABASE=transit\n"
PROBE = '\n__probe:\n\t@printf \'%s\\n\' "$$DATABASE_URL" "$$CLICKHOUSE_PORT" "$$CLICKHOUSE_DATABASE"\n'


def _documented_command() -> str:
    text = (REPO_ROOT / "AGENTS.md").read_text()
    spans = [" ".join(s.split()) for s in re.findall(r"`([^`]*)`", text)]
    matches = [s for s in spans if "make analyze-all" in s and ":5544/" in s]
    assert len(matches) == 1, f"expected one throwaway analyze-all command in AGENTS.md, got {matches}"
    return matches[0]


def test_documented_throwaway_analyze_all_reaches_the_throwaway_stores(tmp_path):
    (tmp_path / "Makefile").write_text((REPO_ROOT / "Makefile").read_text() + PROBE)
    (tmp_path / ".env").write_text(DEV_ENV)
    words = shlex.split(_documented_command())
    env = {k: v for k, v in os.environ.items() if k not in POINTED}
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        name, value = words.pop(0).split("=", 1)
        env[name] = value
    assert words[:2] == ["make", "analyze-all"], words
    result = subprocess.run(
        ["make", "-s", "__probe", *words[2:]], cwd=tmp_path, env=env, capture_output=True, text=True, check=True
    )
    database_url, clickhouse_port, clickhouse_database = result.stdout.splitlines()
    assert ":5544/" in database_url
    assert clickhouse_port == "8124"
    assert clickhouse_database == "transit_test"
