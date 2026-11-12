"""Bring the dev databases up to merged main: migrations, the ClickHouse schema,
analyze-all, then check-aggs, in that order.

This is the one path by which an agent writes to the dev stores (AGENTS.md), so
it refuses everything that could carry unreviewed code or schema into real
data:
- it runs only from the primary checkout, never a linked worktree;
- that checkout's tracked tree is clean, it holds no untracked file a step
  could pick up (`_PICKED_UP`), and its HEAD is origin/main after a fetch;
- one run at a time, under a lock in the git directory;
- with migrations pending, it starts at step 1, since the later steps expect
  the migrated schema.

A pending up migration that `db.migrate.data_losing_tables` flags, one that
drops a table or column or deletes or truncates rows outside the aggregates
analyze rebuilds from source, stops the run before anything is written, until the
owner names that version with --allow-destructive-up. A backfill UPDATE does
not stop it. That function's docstring lists the shapes it does not detect;
catching those is the migration's review.

--check prints the preconditions, the pending migrations and the plan, and
writes nothing. --start N resumes at step N once the earlier steps succeeded.
The guard hook still blocks the writing steps' make targets on a command line;
this script is how they reach dev.

Run from the primary checkout with the poetry environment:
`poetry run python scripts/dev_upgrade.py [--check]`. Each step's output goes to
the terminal and to a log under ~/.cache/transit-app/.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from db.migrate import data_losing_tables, pending_migrations  # noqa: E402  (sys.path injection above)

MIGRATIONS = REPO / "db" / "migrations"
LOG_DIR = pathlib.Path.home() / ".cache" / "transit-app"
STEPS = ("migrate", "ch-bootstrap", "analyze-all", "check-aggs")
# Untracked files a step would run although no review saw them: a module
# Python imports, SQL, anything under db/ (migrate globs db/migrations by
# name), and the makefiles GNU make reads ahead of Makefile.
_PICKED_UP = ("*.py", "*.sql", "db", "GNUmakefile", "makefile")
_URL_USERINFO = re.compile(r"(?<=://)[^/@\s]+@")


class Refusal(Exception):
    """A precondition does not hold; nothing has been written."""


def _git(*args: str, cwd: pathlib.Path = REPO) -> str:
    try:
        done = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as failed:
        first = (failed.stderr or "").strip().splitlines()[:1] or [f"exit {failed.returncode}"]
        # A remote URL in git's message can carry a token.
        raise Refusal(f"git {args[0]} failed in {cwd}: {_URL_USERINFO.sub('', first[0])}") from None
    return done.stdout.strip()


def check_checkout(repo: pathlib.Path = REPO) -> None:
    """Refuse unless *repo* is the primary checkout, clean, at origin/main."""
    git_dir = pathlib.Path(_git("rev-parse", "--absolute-git-dir", cwd=repo))
    common = (repo / _git("rev-parse", "--git-common-dir", cwd=repo)).resolve()
    if git_dir.resolve() != common:
        raise Refusal(f"{repo} is a linked worktree; run from the primary checkout")
    if _git("status", "--porcelain", "--untracked-files=no", cwd=repo):
        raise Refusal(f"{repo} has uncommitted changes to tracked files")
    if untracked := _git("ls-files", "-z", "--others", "--exclude-standard", "--", *_PICKED_UP, cwd=repo):
        paths = untracked.strip("\0").split("\0")
        listed = ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
        raise Refusal(f"{repo} has untracked files a step could pick up: {listed}; commit or remove them")
    _git("fetch", "-q", "origin", "main", cwd=repo)
    head, main = _git("rev-parse", "HEAD", cwd=repo), _git("rev-parse", "origin/main", cwd=repo)
    if head != main:
        raise Refusal(f"HEAD {head[:8]} is not origin/main {main[:8]}; fast-forward the primary checkout first")


@contextlib.contextmanager
def upgrade_lock(repo: pathlib.Path = REPO) -> Iterator[pathlib.Path]:
    """One run at a time: a directory in the git directory, which mkdir
    creates atomically."""
    lock = (repo / _git("rev-parse", "--git-common-dir", cwd=repo)).resolve() / "dev-upgrade.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise Refusal(f"another dev upgrade holds {lock}; remove it only if no run is active") from None
    try:
        yield lock
    finally:
        lock.rmdir()


def destructive_pending(pending: list[str], migrations: pathlib.Path = MIGRATIONS) -> dict[str, list[str]]:
    """Each pending version whose up migration loses data, with the tables it
    loses it from."""
    found = {}
    for version in pending:
        for path in sorted(migrations.glob(f"{version}_*.up.sql")):
            if tables := data_losing_tables(path.read_text()):
                found[path.stem.removesuffix(".up")] = tables
    return found


def dev_database_url(repo: pathlib.Path = REPO) -> str:
    """DATABASE_URL as the Makefile reads it from the checkout's .env: the
    last assignment wins, as Make's `-include .env` makes it."""
    env = repo / ".env"
    url = ""
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and key.strip() == "DATABASE_URL":
                url = value.strip().strip("'\"")
    if not url:
        raise Refusal(f"no DATABASE_URL in {env}")
    return url


def pending_versions(url: str) -> list[str]:
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True)
        return pending_migrations(conn)
    finally:
        conn.close()


def _stop_reason(
    pending: list[str], destructive: dict[str, list[str]], plan: tuple[str, ...], approved: list[str]
) -> str | None:
    """Why a real run must not start, or None."""
    if pending and "migrate" not in plan:
        return "pending migrations; start from step 1, since the later steps expect the migrated schema"
    unapproved = [name for name in destructive if name.split("_")[0] not in approved]
    if unapproved:
        return (
            "pending up migrations lose data: "
            + ", ".join(unapproved)
            + ". The owner names each with --allow-destructive-up VERSION before it runs."
        )
    return None


def step_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment a step runs in: the caller's, less anything that points
    a store. The Makefile exports the stores the checkout's .env names, but a
    key .env leaves unset (CLICKHOUSE_PORT, for one) would pass through from
    the caller, so a shell left pointed at the throwaway pair would send the
    ClickHouse steps there while migrate wrote dev Postgres."""
    return {k: v for k, v in environ.items() if k != "DATABASE_URL" and not k.startswith("CLICKHOUSE_")}


def _run(step: str, log) -> None:
    command = ["make", "-s", step]
    if shutil.which("caffeinate"):
        command = ["caffeinate", "-s", "-i", *command]
    print(f"== {dt.datetime.now():%F %T} {step}", flush=True)
    log.write(f"== {dt.datetime.now():%F %T} {step}\n")
    with subprocess.Popen(
        command, cwd=REPO, env=step_env(os.environ), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
    if proc.returncode:
        raise SystemExit(
            f"{step} failed (exit {proc.returncode}); fix it, then resume with --start {STEPS.index(step) + 1}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="print the plan and write nothing")
    parser.add_argument("--start", type=int, default=1, choices=range(1, len(STEPS) + 1), help="resume at this step")
    parser.add_argument(
        "--allow-destructive-up",
        action="append",
        default=[],
        metavar="VERSION",
        help="the owner's go-ahead for a pending up migration that loses data",
    )
    args = parser.parse_args(argv)
    try:
        check_checkout()
        pending = pending_versions(dev_database_url())
        destructive = destructive_pending(pending)
        print(f"pending migrations: {', '.join(pending) or 'none'}")
        for name, tables in destructive.items():
            print(f"loses data in {', '.join(tables)}: {name}")
        plan = STEPS[args.start - 1 :]
        print(f"plan: {' -> '.join(plan)}")
        stop = _stop_reason(pending, destructive, plan, args.allow_destructive_up)
        if args.check:
            if stop:
                print(f"a real run refuses: {stop}")
            return 0
        if stop:
            raise Refusal(stop)
        with upgrade_lock():
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / f"dev-upgrade-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
            print(f"log: {log_path}")
            with log_path.open("w") as log:
                for step in plan:
                    _run(step, log)
    except Refusal as why:
        print(f"refused: {why}", file=sys.stderr)
        return 3
    print("done. Restart the dev API server so it runs the new code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
