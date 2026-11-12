"""Bring the dev databases up to merged main: migrations, the ClickHouse schema,
analyze-all, then check-aggs, in that order.

This is the one path by which an agent writes to the dev stores (AGENTS.md), so
it refuses everything that could carry unreviewed code or schema into real
data:
- it runs only from the primary checkout, never a linked worktree;
- that checkout's tracked tree is clean, and its HEAD is origin/main after a
  fetch;
- one run at a time, under a lock in the git directory.

A pending up migration that drops, deletes, truncates or updates rows stops the
run before anything is written, unless the owner names that version with
--allow-destructive-up. The check is a conservative reading of the SQL, so a
false stop costs one question and never a dataset.

--check prints the preconditions, the pending migrations and the plan, and
writes nothing. --start N resumes at step N once the earlier steps succeeded.
The guard hook still blocks each of these make targets on a command line; this
script is how they reach dev.

Run from the primary checkout with the poetry environment:
`poetry run python scripts/dev_upgrade.py [--check]`. Each step's output goes to
the terminal and to a log under ~/.cache/transit-app/.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator

REPO = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "db" / "migrations"
LOG_DIR = pathlib.Path.home() / ".cache" / "transit-app"
STEPS = ("migrate", "ch-bootstrap", "analyze-all", "check-aggs")

# A DROP of anything that holds rows: what is not one of these schema objects.
_HARMLESS_DROP = re.compile(
    r"DROP\s+(IF\s+EXISTS\s+)?(CONSTRAINT|DEFAULT|NOT\s+NULL|INDEX|TRIGGER|FUNCTION|VIEW|MATERIALIZED\s+VIEW|POLICY)\b",
    re.IGNORECASE,
)
# A foreign key's ON UPDATE / ON DELETE action is a rule, not a write. The text
# is whitespace-normalised first, so one space separates the words.
_DATA_CHANGE = re.compile(r"\b(DROP|DELETE FROM|TRUNCATE|(?<!ON )UPDATE)\b", re.IGNORECASE)


class Refusal(Exception):
    """A precondition does not hold; nothing has been written."""


def _git(*args: str, cwd: pathlib.Path = REPO) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def check_checkout(repo: pathlib.Path = REPO, *, fetch: bool = True) -> None:
    """Refuse unless *repo* is the primary checkout, clean, at origin/main."""
    git_dir = pathlib.Path(_git("rev-parse", "--absolute-git-dir", cwd=repo))
    common = (repo / _git("rev-parse", "--git-common-dir", cwd=repo)).resolve()
    if git_dir.resolve() != common:
        raise Refusal(f"{repo} is a linked worktree; run from the primary checkout")
    if _git("status", "--porcelain", "--untracked-files=no", cwd=repo):
        raise Refusal(f"{repo} has uncommitted changes to tracked files")
    if fetch:
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


def data_changes(sql: str) -> list[str]:
    """The statements in *sql* that may drop, delete or rewrite rows: every
    DROP but those of schema objects that hold none, and every DELETE FROM,
    TRUNCATE and UPDATE. Comments are left out."""
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    flagged = []
    for statement in code.split(";"):
        text = " ".join(statement.split())
        if not text:
            continue
        for match in _DATA_CHANGE.finditer(text):
            if match.group(1).upper() == "DROP" and _HARMLESS_DROP.match(text, match.start()):
                continue
            flagged.append(text)
            break
    return flagged


def destructive_pending(pending: list[str], migrations: pathlib.Path = MIGRATIONS) -> dict[str, list[str]]:
    """Each pending version whose up migration changes or removes rows, with
    the statements that do."""
    found = {}
    for version in pending:
        for path in sorted(migrations.glob(f"{version}_*.up.sql")):
            if statements := data_changes(path.read_text()):
                found[path.stem.removesuffix(".up")] = statements
    return found


def dev_database_url(repo: pathlib.Path = REPO) -> str:
    """DATABASE_URL as the Makefile reads it, from the checkout's .env."""
    env = repo / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and key.strip() == "DATABASE_URL":
                return value.strip().strip("'\"")
    raise Refusal(f"no DATABASE_URL in {env}")


def pending_versions(url: str) -> list[str]:
    import psycopg2

    sys.path.insert(0, str(REPO))
    from db.migrate import pending_migrations

    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True)
        return pending_migrations(conn)
    finally:
        conn.close()


def _run(step: str, log) -> None:
    command = ["make", "-s", step]
    if shutil.which("caffeinate"):
        command = ["caffeinate", "-s", "-i", *command]
    print(f"== {dt.datetime.now():%F %T} {step}", flush=True)
    log.write(f"== {dt.datetime.now():%F %T} {step}\n")
    with subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
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
        help="the owner's go-ahead for a pending up migration that changes or removes rows",
    )
    args = parser.parse_args(argv)
    try:
        check_checkout()
        pending = pending_versions(dev_database_url())
        destructive = destructive_pending(pending)
        print(f"pending migrations: {', '.join(pending) or 'none'}")
        for name, statements in destructive.items():
            print(f"changes or removes rows: {name}")
            for statement in statements:
                print(f"    {statement[:160]}")
        unapproved = [name for name in destructive if name.split("_")[0] not in args.allow_destructive_up]
        plan = STEPS[args.start - 1 :]
        print(f"plan: {' -> '.join(plan)}")
        if args.check:
            if unapproved:
                print("a real run stops here until the owner names each version above with --allow-destructive-up")
            return 0
        if unapproved and "migrate" in plan:
            raise Refusal(
                "pending up migrations change or remove rows: "
                + ", ".join(unapproved)
                + ". The owner names each with --allow-destructive-up VERSION before it runs."
            )
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
