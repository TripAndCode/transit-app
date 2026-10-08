"""Tests for the daily git hygiene job (local branch/worktree and venv cleanup)."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "daily_git_hygiene.py"
SPEC = importlib.util.spec_from_file_location("daily_git_hygiene", SCRIPT)
assert SPEC and SPEC.loader
hygiene = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hygiene
SPEC.loader.exec_module(hygiene)

cleanup = hygiene.cleanup_git_state


def git(repo: Path, *args: str) -> str:
    """Run Git in a temporary fixture repository."""

    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Create a repository whose local main exactly matches a bare `origin`."""

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(repo))
    git(repo, "config", "user.name", "Hygiene Test")
    git(repo, "config", "user.email", "hygiene@example.com")
    (repo / "tracked.txt").write_text("main\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return repo


# ---------------------------------------------------------------------------
# End-to-end: local cleanup delegates to cleanup_git_state
# ---------------------------------------------------------------------------


def test_run_local_cleanup_fetches_remote_before_validating_base(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """`validate_base` requires local `main` to exactly match `refs/remotes/origin/main`;
    this job must refresh that tracking ref itself rather than trust a stale one, since it
    is specifically meant to run during idle stretches nothing else has refreshed recently.
    """

    # Advance the bare `origin`'s main from a second clone, then fast-forward the
    # fixture repo's local `main` to the same commit WITHOUT fetching -- this leaves
    # `refs/remotes/origin/main` stale relative to both `origin` and local `main`.
    other = tmp_path / "other-clone"
    # `--branch main` is explicit because the bare `remote.git`'s own HEAD symref was
    # never repointed off whatever `init.defaultBranch` picked at `init --bare` time --
    # only `refs/heads/main` itself was ever pushed there.
    git(tmp_path, "clone", "--branch", "main", str(tmp_path / "remote.git"), str(other))
    git(other, "config", "user.name", "Other Clone")
    git(other, "config", "user.email", "other@example.com")
    (other / "tracked.txt").write_text("advanced\n", encoding="utf-8")
    git(other, "commit", "-am", "advance origin main")
    git(other, "push", "origin", "main")
    new_tip = git(other, "rev-parse", "HEAD")

    # `update-ref` requires the target object to already exist in `repository`'s own
    # object database -- it won't fetch it for us. Fetch the bare SHA directly (not
    # through the `origin` remote) so the object lands locally without moving
    # `refs/remotes/origin/main`, which is exactly the ref this test needs to stay stale.
    git(repository, "fetch", str(tmp_path / "remote.git"), new_tip)
    git(repository, "update-ref", "refs/heads/main", new_tip)
    assert git(repository, "rev-parse", "refs/remotes/origin/main") != new_tip

    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})
    log_path = tmp_path / "git-hygiene.log"

    # Must not raise `CleanupError("... does not match origin/main")`: run_local_cleanup's
    # own `git fetch --prune origin` should refresh the tracking ref before build_plan runs.
    hygiene.run_local_cleanup(
        repository, base="main", remote="origin", protected={"main", "production"}, apply=False, log_file=log_path
    )

    assert git(repository, "rev-parse", "refs/remotes/origin/main") == new_tip


def test_run_local_cleanup_logs_deleted_local_branches_after_apply(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Apply deletes a stale local branch and records `cleanup_git_state`'s own DELETED line."""

    git(repository, "branch", "stale-branch", "main")
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_local_cleanup(
        repository, base="main", remote="origin", protected={"main", "production"}, apply=True, log_file=log_path
    )

    assert git(repository, "branch", "--list", "stale-branch") == ""
    log_contents = log_path.read_text(encoding="utf-8")
    assert "local cleanup: DELETED stale-branch" in log_contents


# ---------------------------------------------------------------------------
# Lock file concurrency guard
# ---------------------------------------------------------------------------


def test_lock_round_trip_when_uncontended(tmp_path: Path):
    """Acquiring and releasing an uncontended lock works and frees it for reuse."""

    lock_path = tmp_path / "hygiene.lock"

    handle = hygiene.try_acquire_lock(lock_path)
    assert handle is not None
    hygiene.release_lock(handle)

    # Freed after release: a second acquire must succeed.
    second = hygiene.try_acquire_lock(lock_path)
    assert second is not None
    hygiene.release_lock(second)


def test_lock_skip_when_already_held(tmp_path: Path):
    """A concurrently held lock (simulating another run) is not acquired."""

    lock_path = tmp_path / "hygiene.lock"
    holder = lock_path.open("a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert hygiene.try_acquire_lock(lock_path) is None
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_main_skips_all_cleanup_when_lock_is_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """main() must not touch any git state at all while another run holds the lock."""

    lock_path = tmp_path / "hygiene.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"
    holder = lock_path.open("a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cleanup stage must not run while the lock is held")

    monkeypatch.setattr(hygiene, "run_local_cleanup", _boom)
    monkeypatch.setattr(hygiene, "run_orphaned_venv_pruning", _boom)

    try:
        exit_code = hygiene.main(
            [
                "--repo",
                str(tmp_path),
                "--lock-file",
                str(lock_path),
                "--log-file",
                str(log_path),
                "--state-file",
                str(state_path),
                "--apply",
            ]
        )
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()

    assert exit_code == 0
    assert "SKIP" in log_path.read_text(encoding="utf-8")
    # A lock-collision skip is not a completion -- must still retry later today.
    assert not state_path.exists()


# ---------------------------------------------------------------------------
# Same-day completion marker: turns a lost lock race into an hourly retry
# instead of a 24-hour one (see `already_succeeded_today`'s own docstring).
# ---------------------------------------------------------------------------


def test_already_succeeded_today_false_when_state_file_missing(tmp_path: Path):
    """No prior run recorded at all -- must not be mistaken for success."""

    assert hygiene.already_succeeded_today(tmp_path / "missing") is False


def test_mark_and_check_succeeded_today_round_trip(tmp_path: Path):
    """Marking success today is what the same-day check then reports back."""

    state_path = tmp_path / "last-success"
    hygiene.mark_succeeded_today(state_path, tmp_path / "git-hygiene.log")
    assert hygiene.already_succeeded_today(state_path) is True


def test_already_succeeded_today_false_for_a_stale_prior_day(tmp_path: Path):
    """A completion recorded on an earlier calendar day does not count for today."""

    state_path = tmp_path / "last-success"
    state_path.write_text("2000-01-01\n", encoding="utf-8")
    assert hygiene.already_succeeded_today(state_path) is False


def test_already_succeeded_today_fails_open_on_a_non_missing_os_error(tmp_path: Path):
    """A permission/directory-shape OSError reading the state file must not crash the job.

    Every stage this gates is already idempotent, so treating "can't tell" the same
    as "not yet succeeded" costs at most one redundant hourly retry, not a false skip.
    """

    state_path = tmp_path / "last-success-is-a-directory"
    state_path.mkdir()  # read_text() on a directory raises IsADirectoryError (an OSError)
    assert hygiene.already_succeeded_today(state_path) is False


def test_mark_succeeded_today_logs_instead_of_raising_on_write_failure(tmp_path: Path):
    """A write failure must be logged, not raised -- this runs after cleanup already succeeded."""

    state_path = tmp_path / "last-success-parent-is-a-file"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    blocking_file = state_path.parent / "blocking"
    blocking_file.write_text("", encoding="utf-8")
    unwritable_state_path = blocking_file / "last-success"  # parent.mkdir() fails: not a directory

    log_path = tmp_path / "git-hygiene.log"
    hygiene.mark_succeeded_today(unwritable_state_path, log_path)  # must not raise

    assert "WARNING" in log_path.read_text(encoding="utf-8")


def test_already_succeeded_today_and_mark_succeeded_today_use_the_given_day_not_the_live_clock(
    tmp_path: Path,
):
    """An explicit `today=` must be used verbatim, so a run straddling a UTC midnight stays
    self-consistent between its pre-check and its eventual marker (both pass the same
    captured day, rather than each calling `today_utc()` fresh)."""

    state_path = tmp_path / "last-success"
    log_path = tmp_path / "git-hygiene.log"

    hygiene.mark_succeeded_today(state_path, log_path, today="2026-01-01")

    assert hygiene.already_succeeded_today(state_path, today="2026-01-01") is True
    # A different (e.g. live "today") day must not match yesterday's explicit marker.
    assert hygiene.already_succeeded_today(state_path, today="2026-01-02") is False


def test_main_skips_before_touching_the_lock_when_already_succeeded_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An hourly re-trigger on a day this job already completed must not even try the lock."""

    lock_path = tmp_path / "hygiene.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"
    hygiene.mark_succeeded_today(state_path, log_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("must not attempt the lock once today's run is already marked complete")

    monkeypatch.setattr(hygiene, "try_acquire_lock", _boom)

    exit_code = hygiene.main(
        [
            "--repo",
            str(tmp_path),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--apply",
        ]
    )

    assert exit_code == 0


def test_main_marks_today_succeeded_only_after_a_fully_clean_apply_run(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stage error must not mark today done -- it should retry on the next hourly trigger."""

    lock_path = tmp_path / "hygiene.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise hygiene.HygieneError("simulated stage failure")

    monkeypatch.setattr(hygiene, "run_orphaned_venv_pruning", _boom)

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--venv-root",
            str(tmp_path / "virtualenvs"),
            "--apply",
        ]
    )

    assert exit_code == 2
    assert not state_path.exists()


def test_main_does_not_mark_today_succeeded_on_a_dry_run(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A plain preview (no --apply, the documented default) must not poison the day's real cron run.

    Every stage returns True/exit_code 0 on a dry run just as easily as on a genuinely
    clean --apply run, since each returns before deleting anything -- without the
    `args.apply` gate, an operator previewing the plan by hand would silently cancel
    that day's scheduled --apply cleanup.
    """

    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})

    # The venv-pruning stage's own self-check requires the main checkout's own
    # venv to be a real, enumerable candidate under --venv-root, so this test
    # gives it a real (but otherwise irrelevant to this test's own assertions)
    # venv directory to find, pinned like every other dependency here (lock/
    # log/state) rather than left to poetry's own virtualenvs.path, which may
    # be populated on the machine running it.
    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=1)
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: main_venv.resolve())

    lock_path = tmp_path / "hygiene.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--venv-root",
            str(venv_root),
        ]
    )

    assert exit_code == 0
    assert not state_path.exists()


def test_main_marks_today_succeeded_after_a_fully_clean_apply_run_with_nothing_to_clean(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A real --apply run that finds nothing to delete is still a fully-clean day."""

    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})

    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=1)
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: main_venv.resolve())

    lock_path = tmp_path / "hygiene.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--venv-root",
            str(venv_root),
            "--apply",
        ]
    )

    assert exit_code == 0
    assert hygiene.already_succeeded_today(state_path) is True


def test_main_actually_prunes_an_orphaned_venv_end_to_end(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A populated --venv-root must be reachable through main()'s real stage wiring, not just
    through direct calls to run_orphaned_venv_pruning -- pinning --venv-root to a nonexistent
    directory in the other main() tests (for hermeticity) means every one of them takes the
    stage's very first no-op early-return, so none of them would notice if it were ever
    dropped from main()'s own `stages` tuple entirely. The main checkout's own venv is given
    an OLD mtime here specifically -- a fresh one would be protected by the age margin alone,
    never actually exercising the ownership check this stage depends on to keep it safe."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100)
    monkeypatch.setattr(
        hygiene,
        "poetry_env_path",
        lambda location: main_venv.resolve() if location.resolve() == repository.resolve() else None,
    )

    orphan = _make_fake_venv(venv_root, "transit-delay-app-orphan-py3.12", age_hours=100)

    lock_path = tmp_path / "hygiene.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--venv-root",
            str(venv_root),
            "--min-venv-age-hours",
            "24",
            "--apply",
        ]
    )

    assert exit_code == 0
    assert not orphan.exists()
    assert main_venv.is_dir()  # protected by the ownership check, not merely by age


def test_main_does_not_mark_today_succeeded_when_a_stage_has_a_per_item_failure(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stage that returns False (a swallowed per-item error) must not mark today done either."""

    lock_path = tmp_path / "hygiene.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    monkeypatch.setattr(hygiene, "run_orphaned_venv_pruning", lambda *_args, **_kwargs: False)

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--venv-root",
            str(tmp_path / "virtualenvs"),
            "--apply",
        ]
    )

    assert exit_code == 2
    assert not state_path.exists()


def test_main_venvs_only_prunes_venvs_without_the_git_stage_or_the_completion_marker(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """The post-merge path (`make git-cleanup-apply`) runs after `cleanup_git_state.py` already
    did the git stage, and can follow several merges a day: it must neither repeat that stage
    nor be skipped, or skip a later run, because of the once-per-day marker."""

    def _no_git_stage(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("--venvs-only must not run the branch/worktree stage")

    monkeypatch.setattr(hygiene, "run_local_cleanup", _no_git_stage)
    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100)
    orphan = _make_fake_venv(venv_root, _main_venv_name(tmp_path / "removed-worktree"), age_hours=100)
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: main_venv.resolve())
    state_path = tmp_path / "last-success"
    hygiene.mark_succeeded_today(state_path, tmp_path / "git-hygiene.log")

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(tmp_path / "hygiene.lock"),
            "--log-file",
            str(tmp_path / "git-hygiene.log"),
            "--state-file",
            str(state_path),
            "--venv-root",
            str(venv_root),
            "--venvs-only",
            "--apply",
        ]
    )

    assert exit_code == 0
    assert not orphan.exists()
    assert main_venv.is_dir()


def test_main_reads_the_default_venv_root_from_poetry(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without --venv-root, the stage prunes within poetry's own `virtualenvs.path`, which is
    right on every platform, instead of a hardcoded host path."""

    venv_root = tmp_path / "poetry-virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_virtualenvs_path", lambda: venv_root)
    seen: list[Path] = []

    def _record(_repo: Path, *, venv_root: Path, **_kwargs: object) -> bool:
        seen.append(venv_root)
        return True

    monkeypatch.setattr(hygiene, "run_orphaned_venv_pruning", _record)

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(tmp_path / "hygiene.lock"),
            "--log-file",
            str(tmp_path / "git-hygiene.log"),
            "--venvs-only",
        ]
    )

    assert exit_code == 0
    assert seen == [venv_root]


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess((), returncode=1, stdout="", stderr="poetry broke"),
        subprocess.CompletedProcess((), returncode=0, stdout="\n", stderr=""),
    ],
)
def test_poetry_virtualenvs_path_raises_instead_of_guessing(
    result: subprocess.CompletedProcess[str], monkeypatch: pytest.MonkeyPatch
):
    """A wrong root would silently enumerate nothing, so an unanswerable `poetry config` stops
    the stage rather than falling back to some default."""

    monkeypatch.setattr(hygiene.subprocess, "run", lambda *_a, **_k: result)

    with pytest.raises(hygiene.HygieneError, match="--venv-root"):
        hygiene.poetry_virtualenvs_path()


def test_poetry_virtualenvs_path_expands_the_configured_path(monkeypatch: pytest.MonkeyPatch):
    """`poetry config virtualenvs.path` can report a `~`-relative path."""

    monkeypatch.setattr(
        hygiene.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess((), returncode=0, stdout="~/venvs\n", stderr=""),
    )

    assert hygiene.poetry_virtualenvs_path() == Path.home() / "venvs"


# ---------------------------------------------------------------------------
# Orphaned poetry venv pruning
# ---------------------------------------------------------------------------


def _main_venv_name(checkout: Path, python: str = "3.12") -> str:
    """The venv directory name Poetry gives `checkout` for one interpreter."""

    return f"{hygiene.poetry_venv_name_prefix(checkout)}{python}"


def _make_fake_venv(root: Path, name: str, *, age_hours: float) -> Path:
    """Create a directory standing in for a poetry venv, with a specific mtime."""

    venv = root / name
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    stamp = time.time() - age_hours * 3600
    os.utime(venv, (stamp, stamp))
    return venv


def test_poetry_env_path_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch):
    """A location with no resolvable poetry venv (not a poetry project, no venv yet) is not an error."""

    monkeypatch.setattr(
        hygiene.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess((), returncode=1, stdout="", stderr=""),
    )
    assert hygiene.poetry_env_path(Path("/unused")) is None


def test_poetry_env_path_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch):
    """A hanging `poetry env info` must not hang this job -- treated the same as unresolvable,
    never left to hold the hygiene lock indefinitely."""

    def _hangs(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="poetry", timeout=hygiene.POETRY_COMMAND_TIMEOUT_SECONDS)

    monkeypatch.setattr(hygiene.subprocess, "run", _hangs)
    assert hygiene.poetry_env_path(Path("/unused")) is None


def test_poetry_env_path_returns_resolved_path_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A successful `poetry env info --path` is parsed and resolved."""

    venv = tmp_path / "some-venv"
    venv.mkdir()
    monkeypatch.setattr(
        hygiene.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess((), returncode=0, stdout=f"{venv}\n", stderr=""),
    )
    assert hygiene.poetry_env_path(Path("/unused")) == venv.resolve()


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        # Generated by Poetry's own `EnvManager.generate_env_name("transit-delay-app", location)`,
        # identical under Poetry 1.8 and 2.x.
        ("/srv/transit-app", "transit-delay-app-aO8oPM_b-py"),
        ("/srv/transit-app/.worktrees/fix-item-1", "transit-delay-app-kBygU0lo-py"),
    ],
)
def test_poetry_venv_name_prefix_matches_poetrys_own_naming(location: str, expected: str):
    """Ownership rests entirely on reproducing Poetry's venv naming, so it is pinned to names
    Poetry itself produced rather than to a re-derivation of the same formula."""

    assert hygiene.poetry_venv_name_prefix(Path(location)) == expected


def test_compute_in_use_poetry_venvs_keeps_a_worktrees_venv_by_name_without_asking_poetry(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A worktree's venv is in use because its name carries the worktree's path hash; poetry is
    never spawned per worktree, so a poetry hiccup there can no longer hide a real venv."""

    worktree_path = tmp_path / "extra-worktree"
    git(repository, "worktree", "add", "-b", "fix/item-99", str(worktree_path), "main")
    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100)
    worktree_venvs = {
        _make_fake_venv(venv_root, _main_venv_name(worktree_path, python), age_hours=100).resolve()
        for python in ("3.12", "3.14")
    }
    orphan = _make_fake_venv(venv_root, _main_venv_name(tmp_path / "removed-worktree"), age_hours=100)

    def _no_poetry(_location: Path) -> Path | None:
        raise AssertionError("ownership must not depend on spawning poetry in a worktree")

    monkeypatch.setattr(hygiene, "poetry_env_path", _no_poetry)

    in_use = hygiene.compute_in_use_poetry_venvs(repository, main_venv.resolve(), venv_root=venv_root)

    assert in_use == {main_venv.resolve(), *worktree_venvs}
    assert orphan.resolve() not in in_use


def test_compute_in_use_poetry_venvs_does_not_raise_for_a_worktree_that_never_created_a_venv(
    tmp_path: Path, repository: Path
):
    """A worktree that routes `poetry run` to the main checkout's venv owns nothing and blocks
    nothing, however old it is."""

    worktree_path = tmp_path / "extra-worktree"
    git(repository, "worktree", "add", "-b", "fix/item-99", str(worktree_path), "main")
    old_stamp = time.time() - 1000 * 3600
    os.utime(worktree_path / ".git", (old_stamp, old_stamp))
    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100).resolve()

    assert hygiene.compute_in_use_poetry_venvs(repository, main_venv, venv_root=venv_root) == {main_venv}


def test_compute_in_use_poetry_venvs_releases_a_prunable_worktrees_venv(tmp_path: Path, repository: Path):
    """A worktree whose directory was removed out-of-band (git marks the entry `prunable`) owns
    nothing any more, so its venv is an orphan."""

    worktree_path = tmp_path / "extra-worktree"
    git(repository, "worktree", "add", "-b", "fix/item-99", str(worktree_path), "main")
    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100).resolve()
    stale_venv = _make_fake_venv(venv_root, _main_venv_name(worktree_path), age_hours=100)
    shutil.rmtree(worktree_path)
    assert "prunable" in git(repository, "worktree", "list", "--porcelain")

    in_use = hygiene.compute_in_use_poetry_venvs(repository, main_venv, venv_root=venv_root)

    assert stale_venv.resolve() not in in_use


def test_compute_in_use_poetry_venvs_keeps_a_locked_worktrees_venv_when_its_directory_is_absent(
    tmp_path: Path, repository: Path
):
    """Git never marks a locked worktree prunable, even with its directory gone: locking exists
    to protect it (e.g. on a detached drive) from exactly this kind of cleanup."""

    worktree_path = tmp_path / "extra-worktree"
    git(repository, "worktree", "add", "-b", "fix/item-99", str(worktree_path), "main")
    git(repository, "worktree", "lock", str(worktree_path))
    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100).resolve()
    locked_venv = _make_fake_venv(venv_root, _main_venv_name(worktree_path), age_hours=100)
    shutil.rmtree(worktree_path)
    listing = git(repository, "worktree", "list", "--porcelain")
    assert "locked" in listing
    assert "prunable" not in listing

    in_use = hygiene.compute_in_use_poetry_venvs(repository, main_venv, venv_root=venv_root)

    assert locked_venv.resolve() in in_use


def test_compute_in_use_poetry_venvs_refuses_when_the_main_venv_does_not_carry_the_predicted_hash(
    tmp_path: Path, repository: Path
):
    """If poetry's own venv for the main checkout does not match the name this module predicts
    for it, Poetry's naming has changed and every ownership answer would be wrong."""

    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, "transit-delay-app-NotAHash-py3.12", age_hours=100).resolve()

    with pytest.raises(hygiene.HygieneError, match="path hashing predicts"):
        hygiene.compute_in_use_poetry_venvs(repository, main_venv, venv_root=venv_root)


def test_run_orphaned_venv_pruning_raises_when_the_checkouts_own_venv_is_unresolvable(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """If poetry can't resolve this checkout's own venv while this project's venvs do exist
    under --venv-root, nothing anchors the naming check, so the stage refuses rather than
    treating them all as orphans."""

    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: None)
    venv_root = tmp_path / "virtualenvs"
    unattributable = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100)

    with pytest.raises(hygiene.HygieneError, match="could not resolve"):
        hygiene.run_orphaned_venv_pruning(
            repository,
            venv_root=venv_root,
            min_age_hours=24,
            max_deletes_per_run=10,
            apply=True,
            log_file=tmp_path / "log",
        )
    assert unattributable.is_dir()


def test_run_orphaned_venv_pruning_is_a_no_op_for_a_clone_with_no_venv_at_all(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A clone where nothing ever ran poetry (no venv for the checkout, none of this project's
    under the root) has nothing to prune, so a post-merge cleanup there must not fail."""

    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: None)

    assert (
        hygiene.run_orphaned_venv_pruning(
            repository,
            venv_root=tmp_path / "does-not-exist",
            min_age_hours=24,
            max_deletes_per_run=10,
            apply=True,
            log_file=tmp_path / "log",
        )
        is True
    )
    assert not (tmp_path / "log").exists()


def test_run_orphaned_venv_pruning_anchors_on_the_primary_worktree_when_invoked_from_a_linked_one(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """`make git-cleanup` may run from any worktree, but only the primary one owns a real venv
    when worktrees route `poetry run` to it, so that is where the anchoring venv is resolved."""

    linked = tmp_path / "linked-worktree"
    git(repository, "worktree", "add", "-b", "fix/item-99", str(linked), "main")
    venv_root = tmp_path / "virtualenvs"
    main_venv = _make_fake_venv(venv_root, _main_venv_name(repository), age_hours=100)
    orphan = _make_fake_venv(venv_root, _main_venv_name(tmp_path / "removed-worktree"), age_hours=100)
    queried: list[Path] = []

    def _fake_env_path(location: Path) -> Path | None:
        queried.append(location.resolve())
        return main_venv.resolve() if location.resolve() == repository.resolve() else None

    monkeypatch.setattr(hygiene, "poetry_env_path", _fake_env_path)

    all_clean = hygiene.run_orphaned_venv_pruning(
        linked, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=True, log_file=tmp_path / "log"
    )

    assert all_clean is True
    assert queried == [repository.resolve()]
    assert main_venv.is_dir()
    assert not orphan.exists()


def test_run_orphaned_venv_pruning_raises_when_venv_root_does_not_exist_but_a_real_venv_exists_elsewhere(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A --venv-root that simply doesn't exist (e.g. left at the wrong platform's default) must
    not take a silent no-op return before the self-check runs -- this is precisely the
    misconfiguration the self-check exists to catch, and it must be caught even though the
    directory itself is absent, not merely wrong-but-present."""

    real_venv = _make_fake_venv(tmp_path / "real-location", "transit-delay-app-main-py3.12", age_hours=1)
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: real_venv)

    with pytest.raises(hygiene.HygieneError, match="does not enumerate"):
        hygiene.run_orphaned_venv_pruning(
            repository,
            venv_root=tmp_path / "does-not-exist-virtualenvs",  # e.g. the wrong platform's default
            min_age_hours=24,
            max_deletes_per_run=10,
            apply=True,
            log_file=tmp_path / "log",
        )


def test_run_orphaned_venv_pruning_refuses_a_venv_root_containing_none_of_the_in_use_venvs(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A misconfigured --venv-root (wrong platform default, a renamed project's glob matching
    nothing) must not silently "succeed" by finding zero candidates forever -- that would
    enforce nothing while looking clean, the exact failure mode this whole feature exists to
    prevent a repeat of. The main checkout's own venv must live somewhere under --venv-root."""

    venv_root = tmp_path / "virtualenvs"
    venv_root.mkdir()  # exists, but empty and unrelated to where the real venv actually lives
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: tmp_path / "elsewhere" / "venv-main")

    with pytest.raises(hygiene.HygieneError):
        hygiene.run_orphaned_venv_pruning(
            repository,
            venv_root=venv_root,
            min_age_hours=24,
            max_deletes_per_run=10,
            apply=True,
            log_file=tmp_path / "log",
        )


def test_run_orphaned_venv_pruning_refuses_a_venv_root_that_is_only_an_ancestor(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """`Path.parents` includes every ancestor, not just the immediate containing directory --
    --venv-root must be main_venv's DIRECT parent, not merely some directory above it (e.g. the
    real venvs dir's own parent), or this self-check would accept a --venv-root that can never
    actually contain the venvs it's supposed to enumerate."""

    venv_root = tmp_path / "virtualenvs"
    real_venv_dir = venv_root / "nested"
    real_venv_dir.mkdir(parents=True)
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: real_venv_dir / "transit-delay-app-main-py3.12")

    with pytest.raises(hygiene.HygieneError):
        hygiene.run_orphaned_venv_pruning(
            repository,
            venv_root=venv_root,  # an ancestor of main_venv, but not its direct parent
            min_age_hours=24,
            max_deletes_per_run=10,
            apply=True,
            log_file=tmp_path / "log",
        )


def test_run_orphaned_venv_pruning_refuses_when_main_venv_name_does_not_match_the_glob(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A renamed project (pyproject.toml's `name` changed without updating `POETRY_VENV_GLOB`)
    must be caught here too, not just a wrong --venv-root -- otherwise this check would pass
    while the stage's own glob enumeration matches nothing, forever."""

    venv_root = tmp_path / "virtualenvs"
    venv_root.mkdir()
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "some-renamed-project-py3.12")

    with pytest.raises(hygiene.HygieneError):
        hygiene.run_orphaned_venv_pruning(
            repository,
            venv_root=venv_root,
            min_age_hours=24,
            max_deletes_per_run=10,
            apply=True,
            log_file=tmp_path / "log",
        )


def test_run_orphaned_venv_pruning_dry_run_deletes_nothing(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without --apply, planning must not delete any venv nor write the log."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    orphaned = _make_fake_venv(venv_root, "transit-delay-app-orphan-py3.12", age_hours=100)
    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo, _main_venv, **_kwargs: set())

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=False, log_file=log_path
    )

    assert all_clean is True
    assert orphaned.is_dir()
    assert not log_path.exists()


def test_run_orphaned_venv_pruning_ignores_non_matching_venv_names(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """POETRY_VENV_GLOB must never match an unrelated project's venv sharing the same
    --venv-root (poetry's default virtualenvs.path is shared across every project on a host)."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    unrelated = _make_fake_venv(venv_root, "some-other-project-abc123-py3.12", age_hours=1000)
    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo, _main_venv, **_kwargs: set())

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=True, log_file=log_path
    )

    assert all_clean is True
    assert unrelated.is_dir()  # never even considered, let alone deleted
    assert not log_path.exists()


def test_run_orphaned_venv_pruning_deletes_old_orphans_keeps_in_use_and_young(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only a venv that is BOTH unmatched by any current worktree AND older than the
    safety margin gets deleted; an in-use one and a too-young orphan are both kept."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    in_use_venv = _make_fake_venv(venv_root, "transit-delay-app-inuse-py3.12", age_hours=1000)
    young_orphan = _make_fake_venv(venv_root, "transit-delay-app-young-py3.12", age_hours=1)
    old_orphan = _make_fake_venv(venv_root, "transit-delay-app-old-py3.12", age_hours=100)

    monkeypatch.setattr(
        hygiene, "compute_in_use_poetry_venvs", lambda _repo, _main_venv, **_kwargs: {in_use_venv.resolve()}
    )

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=True, log_file=log_path
    )

    assert all_clean is True
    assert in_use_venv.is_dir()
    assert young_orphan.is_dir()
    assert not old_orphan.exists()
    assert "DELETED" in log_path.read_text(encoding="utf-8")
    assert "transit-delay-app-old-py3.12" in log_path.read_text(encoding="utf-8")


def test_run_orphaned_venv_pruning_refuses_a_plan_larger_than_the_cap(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A plan exceeding --max-venv-deletes-per-run is a circuit breaker, not steady-state --
    refuses to delete anything rather than silently applying an unusually large batch."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    orphans = [_make_fake_venv(venv_root, f"transit-delay-app-orphan-{i}-py3.12", age_hours=100) for i in range(3)]
    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo, _main_venv, **_kwargs: set())

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=2, apply=True, log_file=log_path
    )

    assert all_clean is False
    assert all(orphan.is_dir() for orphan in orphans)  # nothing deleted
    assert "REFUSING" in log_path.read_text(encoding="utf-8")


def test_run_orphaned_venv_pruning_dry_run_over_cap_does_not_refuse_for_real(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A dry run over the cap must not behave like a real refusal: it returns True (not False --
    a preview can't manufacture a nonzero exit code) and writes no log line (the cap is only
    ever enforced for real on an --apply run), while still warning on stderr so the preview
    isn't silently misleading about what --apply would then do."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    orphans = [_make_fake_venv(venv_root, f"transit-delay-app-orphan-{i}-py3.12", age_hours=100) for i in range(3)]
    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo, _main_venv, **_kwargs: set())

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=2, apply=False, log_file=log_path
    )

    assert all_clean is True
    assert all(orphan.is_dir() for orphan in orphans)  # nothing deleted
    assert not log_path.exists()  # only a real --apply refusal gets logged


def test_run_orphaned_venv_pruning_never_deletes_a_symlinked_candidate(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A symlink under --venv-root is excluded from consideration entirely, not merely refused
    at delete time -- shutil.rmtree already refuses a top-level symlink, but leaving it in the
    candidate list would permanently set all_clean=False (and so block the completion marker)
    on every single run for as long as the symlink exists."""

    venv_root = tmp_path / "virtualenvs"
    venv_root.mkdir()
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    real_target = _make_fake_venv(tmp_path / "elsewhere", "transit-delay-app-real-py3.12", age_hours=100)
    symlinked = venv_root / "transit-delay-app-symlink-py3.12"
    symlinked.symlink_to(real_target, target_is_directory=True)
    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo, _main_venv, **_kwargs: set())

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=True, log_file=log_path
    )

    assert all_clean is True  # never considered, so never causes a delete failure
    assert symlinked.is_symlink()
    assert real_target.exists()  # the real target is untouched regardless of the symlink


def test_run_orphaned_venv_pruning_skips_the_recheck_when_nothing_is_deletable(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """When planning finds nothing to delete, the apply-time fresh in-use re-check (its own
    poetry subprocess spawns per worktree) must not run at all -- there's nothing for it to
    protect against."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    in_use_venv = _make_fake_venv(venv_root, "transit-delay-app-inuse-py3.12", age_hours=1000)

    calls = {"n": 0}

    def _fake_in_use(_repo: Path, _main_venv: Path, **_kwargs: object) -> set[Path]:
        calls["n"] += 1
        return {in_use_venv.resolve()}

    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", _fake_in_use)

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=True, log_file=log_path
    )

    assert all_clean is True
    assert calls["n"] == 1  # only the planning call, no apply-time re-check


def test_run_orphaned_venv_pruning_rechecks_before_apply(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A venv that became in-use between planning and apply must not be deleted --
    the plan is re-checked immediately before deleting."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    candidate = _make_fake_venv(venv_root, "transit-delay-app-newly-adopted-py3.12", age_hours=100)

    calls = {"n": 0}

    def _fake_in_use(_repo: Path, _main_venv: Path, **_kwargs: object) -> set[Path]:
        calls["n"] += 1
        # Planning (1st call) sees it as orphaned; the apply-time re-check (2nd call)
        # sees it as now in use, as if a new worktree had just adopted this exact venv.
        return set() if calls["n"] == 1 else {candidate.resolve()}

    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", _fake_in_use)

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=True, log_file=log_path
    )

    assert all_clean is True
    assert candidate.is_dir()
    assert "SKIPPED" in log_path.read_text(encoding="utf-8")
    assert calls["n"] == 2


def test_run_orphaned_venv_pruning_continues_after_one_delete_error(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A delete failure on one orphaned venv must not stop the rest of the stage."""

    venv_root = tmp_path / "virtualenvs"
    monkeypatch.setattr(hygiene, "poetry_env_path", lambda *_a, **_k: venv_root / "transit-delay-app-main-py3.12")
    (venv_root / "transit-delay-app-main-py3.12").mkdir(parents=True, exist_ok=True)
    flaky = _make_fake_venv(venv_root, "transit-delay-app-flaky-py3.12", age_hours=100)
    fine = _make_fake_venv(venv_root, "transit-delay-app-fine-py3.12", age_hours=100)

    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo, _main_venv, **_kwargs: set())

    real_rmtree = shutil.rmtree

    def _flaky_rmtree(path: object, *args: object, **kwargs: object) -> None:
        if Path(str(path)).name == "transit-delay-app-flaky-py3.12":
            raise OSError("simulated transient error")
        real_rmtree(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(hygiene.shutil, "rmtree", _flaky_rmtree)

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, max_deletes_per_run=10, apply=True, log_file=log_path
    )

    assert all_clean is False  # the swallowed per-venv error must still surface to the caller
    assert flaky.is_dir()
    assert not fine.exists()
    log_contents = log_path.read_text(encoding="utf-8")
    assert "ERROR deleting" in log_contents
    assert "DELETED" in log_contents
