"""Tests for scripts/collect_github_status.py: the GitHub collector for the `github`
operations-status component (item 121)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "collect_github_status.py"
SPEC = importlib.util.spec_from_file_location("collect_github_status", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)

T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
REPO_SLUG = "TripAndCode/transit-app"


@dataclass
class FakeCompletedProcess:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def runner_from(responses: dict[str, FakeCompletedProcess]):
    """Build a fake `Runner` keyed by ` `-joined argv prefix, raising an assertion error
    if an unexpected command is issued -- see collect_vps_status.py's identical helper."""

    def runner(cmd):
        key = " ".join(cmd)
        for prefix, response in responses.items():
            if key.startswith(prefix):
                return response
        raise AssertionError(f"unexpected command: {cmd!r}")

    return runner


def failing_runner(exc: Exception):
    def runner(cmd):
        raise exc

    return runner


# --- redact_secrets -------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "token ghp_abcdefghijklmnopqrstuvwxyz012345 leaked",
        "installation token ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmn leaked",
        "Authorization: Bearer sk-not-a-real-secret-but-long-enough-1234567890",
    ],
)
def test_redact_secrets_scrubs_token_like_material(text):
    redacted = collector.redact_secrets(text)
    assert "[REDACTED]" in redacted
    assert "ghp_" not in redacted
    assert "ghs_" not in redacted
    assert "github_pat_" not in redacted


def test_redact_secrets_leaves_ordinary_text_untouched():
    text = "gh: authentication failed for repo TripAndCode/transit-app"
    assert collector.redact_secrets(text) == text


# --- resolve_repo_slug / _parse_github_slug --------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/TripAndCode/transit-app.git", REPO_SLUG),
        ("https://github.com/TripAndCode/transit-app", REPO_SLUG),
        ("git@github.com:TripAndCode/transit-app.git", REPO_SLUG),
        ("git@gitlab.com:someone/other.git", None),
        ("not a url at all", None),
    ],
)
def test_parse_github_slug(url, expected):
    assert collector._parse_github_slug(url) == expected


def test_resolve_repo_slug_from_git_remote():
    runner = runner_from(
        {"git -C /repo remote get-url origin": FakeCompletedProcess(0, stdout="https://github.com/a/b.git\n")}
    )
    assert collector.resolve_repo_slug(Path("/repo"), git_runner=runner) == "a/b"


def test_resolve_repo_slug_none_when_not_a_repo():
    runner = runner_from({"git -C /repo remote get-url origin": FakeCompletedProcess(128, stderr="not a repo")})
    assert collector.resolve_repo_slug(Path("/repo"), git_runner=runner) is None


def test_resolve_repo_slug_none_when_git_missing():
    runner = failing_runner(FileNotFoundError("git not found"))
    assert collector.resolve_repo_slug(Path("/repo"), git_runner=runner) is None


# --- diagnose_gh_failure ----------------------------------------------------------


@pytest.mark.parametrize(
    "stderr,expected_kind",
    [
        ("gh: Bad credentials (HTTP 401)", "auth_error"),
        ("gh: authentication required, run `gh auth login`", "auth_error"),
        ("gh: API rate limit exceeded (HTTP 403)", "rate_limited"),
        ("curl: (6) Could not resolve host: api.github.com", "network_error"),
        ("context deadline exceeded (timeout)", "network_error"),
        ("gh: Not Found (HTTP 404)", "not_found"),
        ("gh: something unexpected happened", "unknown_error"),
    ],
)
def test_diagnose_gh_failure_classifies(stderr, expected_kind):
    kind, _excerpt = collector.diagnose_gh_failure(stderr)
    assert kind == expected_kind


def test_diagnose_gh_failure_redacts_and_bounds_excerpt():
    stderr = "gh: request failed using token ghp_abcdefghijklmnopqrstuvwxyz012345"
    _kind, excerpt = collector.diagnose_gh_failure(stderr)
    assert "ghp_" not in excerpt
    assert "[REDACTED]" in excerpt
    assert len(excerpt) <= collector.ops_status.MAX_DETAIL_STRING_LENGTH


# --- fetch_open_prs ---------------------------------------------------------------

# Recorded (field-trimmed, token-free) fixture shaped exactly like a real
# `gh pr list --json number,title,isDraft,mergeable,mergeStateStatus,headRefName,
# updatedAt,statusCheckRollup` response against this repo.
OPEN_PRS_FIXTURE = json.dumps(
    [
        {
            "number": 400,
            "title": "feat(oracle): publish an Oracle collector heartbeat",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "headRefName": "vps-loop/item-119",
            "updatedAt": "2026-09-11T09:42:05Z",
            "statusCheckRollup": [],
        },
        {
            "number": 401,
            "title": "feat(ops): collect VPS and Claude-loop status",
            "isDraft": True,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "DRAFT",
            "headRefName": "vps-loop/item-120",
            "updatedAt": "2026-09-11T10:05:00Z",
            "statusCheckRollup": [],
        },
        {
            "number": 402,
            "title": "fix(frontend): simplify comparison explanation layout",
            "isDraft": False,
            "mergeable": "CONFLICTING",
            "mergeStateStatus": "DIRTY",
            "headRefName": "fix/dark-mode-ui-pass",
            "updatedAt": "2026-09-11T08:00:00Z",
            "statusCheckRollup": [
                {"name": "backend-tests", "status": "COMPLETED", "conclusion": "SUCCESS", "isRequired": True}
            ],
        },
        {
            "number": 403,
            "title": "fix(frontend): brighten dark theme text colors",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "headRefName": "fix/dark-mode-ui-pass-2",
            "updatedAt": "2026-09-11T08:10:00Z",
            "statusCheckRollup": [
                {"name": "backend-tests", "status": "COMPLETED", "conclusion": "FAILURE", "isRequired": True},
                {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS", "isRequired": False},
            ],
        },
        {
            "number": 404,
            "title": "chore: unrelated optional check failure",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "headRefName": "chore/unrelated",
            "updatedAt": "2026-09-11T08:20:00Z",
            "statusCheckRollup": [
                {"name": "optional-canary", "status": "COMPLETED", "conclusion": "FAILURE", "isRequired": False}
            ],
        },
    ]
)


def test_fetch_open_prs_parses_fixture():
    runner = runner_from({"gh pr list": FakeCompletedProcess(0, stdout=OPEN_PRS_FIXTURE)})
    prs, error_kind, error_detail = collector.fetch_open_prs(repo_slug=REPO_SLUG, limit=50, runner=runner)

    assert error_kind is None
    assert error_detail == ""
    assert len(prs) == 5


def test_fetch_open_prs_degrades_on_gh_failure():
    runner = runner_from({"gh pr list": FakeCompletedProcess(1, stderr="gh: Bad credentials (HTTP 401)")})
    prs, error_kind, _detail = collector.fetch_open_prs(repo_slug=REPO_SLUG, limit=50, runner=runner)

    assert prs is None
    assert error_kind == "auth_error"


def test_fetch_open_prs_degrades_on_invalid_json():
    runner = runner_from({"gh pr list": FakeCompletedProcess(0, stdout="not json")})
    prs, error_kind, _detail = collector.fetch_open_prs(repo_slug=REPO_SLUG, limit=50, runner=runner)

    assert prs is None
    assert error_kind == "invalid_response"


def test_fetch_open_prs_degrades_when_gh_missing():
    runner = failing_runner(FileNotFoundError("gh not found"))
    prs, error_kind, _detail = collector.fetch_open_prs(repo_slug=REPO_SLUG, limit=50, runner=runner)

    assert prs is None
    assert error_kind == "gh_unavailable"


def test_fetch_open_prs_degrades_on_timeout():
    runner = failing_runner(subprocess.TimeoutExpired(cmd="gh", timeout=20))
    prs, error_kind, _detail = collector.fetch_open_prs(repo_slug=REPO_SLUG, limit=50, runner=runner)

    assert prs is None
    assert error_kind == "gh_unavailable"


def test_fetch_open_prs_never_uses_f_flag():
    """`gh api`/`gh pr list` switch to POST the moment `-f`/`-F` is present; PR listing
    must stay a pure read."""
    runner = runner_from({"gh pr list": FakeCompletedProcess(0, stdout="[]")})
    captured = []

    def spying_runner(cmd):
        captured.append(cmd)
        return runner(cmd)

    collector.fetch_open_prs(repo_slug=REPO_SLUG, limit=50, runner=spying_runner)
    assert "-f" not in captured[0]
    assert "-F" not in captured[0]


# --- fetch_branch_protection -------------------------------------------------------

# Recorded (trimmed) fixture shaped like a real `gh api repos/{slug}/branches` page.
BRANCHES_FIXTURE = json.dumps(
    [
        {"name": "main", "commit": {"sha": "abc123"}, "protected": False},
        {"name": "production", "commit": {"sha": "def456"}, "protected": False},
        {"name": "fix/dark-mode-ui-pass", "commit": {"sha": "111111"}, "protected": False},
        {"name": "vps-loop/item-129", "commit": {"sha": "222222"}, "protected": True},
    ]
)


def test_fetch_branch_protection_parses_fixture():
    runner = runner_from({"gh api": FakeCompletedProcess(0, stdout=BRANCHES_FIXTURE)})
    mapping = collector.fetch_branch_protection(repo_slug=REPO_SLUG, limit=100, runner=runner)

    assert mapping == {
        "main": False,
        "production": False,
        "fix/dark-mode-ui-pass": False,
        "vps-loop/item-129": True,
    }


def test_fetch_branch_protection_none_on_failure():
    runner = runner_from({"gh api": FakeCompletedProcess(1, stderr="gh: Not Found (HTTP 404)")})
    assert collector.fetch_branch_protection(repo_slug=REPO_SLUG, limit=100, runner=runner) is None


def test_fetch_branch_protection_uses_query_param_not_f_flag():
    runner = runner_from({"gh api": FakeCompletedProcess(0, stdout="[]")})
    captured = []

    def spying_runner(cmd):
        captured.append(cmd)
        return runner(cmd)

    collector.fetch_branch_protection(repo_slug=REPO_SLUG, limit=100, runner=spying_runner)
    joined = " ".join(captured[0])
    assert "-f" not in captured[0]
    assert "-F" not in captured[0]
    assert "per_page=100" in joined


# --- summarize_prs -----------------------------------------------------------------


def test_summarize_prs_counts_draft_conflicting_and_failing_checks():
    prs = json.loads(OPEN_PRS_FIXTURE)
    summary = collector.summarize_prs(prs, limit=50)

    assert summary["open_pr_count"] == 5
    assert summary["open_pr_list_truncated"] is False
    assert summary["draft_pr_count"] == 1
    assert summary["conflicting_pr_count"] == 1
    assert summary["conflicting_pr_numbers"] == [402]
    assert summary["failing_checks_pr_count"] == 2
    assert summary["failing_checks_pr_numbers"] == [403, 404]
    # Only PR 403's failing check is marked required; PR 404's is an optional canary.
    assert summary["failing_required_checks_pr_count"] == 1


def test_summarize_prs_marks_truncated_when_at_limit():
    prs = json.loads(OPEN_PRS_FIXTURE)
    summary = collector.summarize_prs(prs, limit=5)
    assert summary["open_pr_list_truncated"] is True


def test_summarize_prs_caps_number_lists_at_contract_bound():
    prs = [{"number": n, "isDraft": False, "mergeable": "CONFLICTING", "statusCheckRollup": []} for n in range(1, 30)]
    summary = collector.summarize_prs(prs, limit=50)
    assert summary["conflicting_pr_count"] == 29
    assert len(summary["conflicting_pr_numbers"]) == collector.ops_status.MAX_DETAIL_LIST_LENGTH


# --- gather_stale_branches ----------------------------------------------------------

FOR_EACH_REF_FIXTURE = "\n".join(
    [
        "origin\t2026-09-11T22:01:27+09:00",
        "origin/HEAD\t2026-09-11T22:01:27+09:00",
        "origin/main\t2026-09-11T12:00:00+00:00",
        "origin/production\t2026-08-01T00:00:00+00:00",
        "origin/fix/dark-mode-ui-pass\t2026-09-10T12:00:00+00:00",
        "origin/vps-loop/item-1\t2026-06-01T00:00:00+00:00",
        "origin/vps-loop/item-2\t2026-07-01T00:00:00+00:00",
    ]
)


def test_gather_stale_branches_filters_protected_and_recent():
    runner = runner_from({"git -C /repo for-each-ref": FakeCompletedProcess(0, stdout=FOR_EACH_REF_FIXTURE)})
    stale = collector.gather_stale_branches(
        repo=Path("/repo"),
        now=T0,
        protected_names=frozenset({"main", "production"}),
        remote_protection=None,
        stale_days=30.0,
        max_branches=10,
        git_runner=runner,
    )

    # "main"/"production" excluded as always-protected; "fix/dark-mode-ui-pass" is
    # only ~1 day old (not stale); the two vps-loop branches are both >30 days old.
    assert stale == ("vps-loop/item-1", "vps-loop/item-2")


def test_gather_stale_branches_respects_remote_protection_flag():
    runner = runner_from({"git -C /repo for-each-ref": FakeCompletedProcess(0, stdout=FOR_EACH_REF_FIXTURE)})
    stale = collector.gather_stale_branches(
        repo=Path("/repo"),
        now=T0,
        protected_names=frozenset({"main", "production"}),
        remote_protection={"vps-loop/item-1": True},
        stale_days=30.0,
        max_branches=10,
        git_runner=runner,
    )

    assert stale == ("vps-loop/item-2",)


def test_gather_stale_branches_caps_at_max_branches_oldest_first():
    lines = [f"origin/branch-{i}\t2026-01-01T00:00:00+00:00" for i in range(15)]
    runner = runner_from({"git -C /repo for-each-ref": FakeCompletedProcess(0, stdout="\n".join(lines))})
    stale = collector.gather_stale_branches(
        repo=Path("/repo"),
        now=T0,
        protected_names=frozenset(),
        remote_protection=None,
        stale_days=30.0,
        max_branches=10,
        git_runner=runner,
    )

    assert len(stale) == 10


def test_gather_stale_branches_none_on_git_failure():
    runner = runner_from({"git -C /repo for-each-ref": FakeCompletedProcess(128, stderr="not a repo")})
    stale = collector.gather_stale_branches(
        repo=Path("/repo"),
        now=T0,
        protected_names=frozenset(),
        remote_protection=None,
        stale_days=30.0,
        max_branches=10,
        git_runner=runner,
    )
    assert stale is None


def test_gather_stale_branches_none_when_git_missing():
    runner = failing_runner(FileNotFoundError("git not found"))
    stale = collector.gather_stale_branches(
        repo=Path("/repo"),
        now=T0,
        protected_names=frozenset(),
        remote_protection=None,
        stale_days=30.0,
        max_branches=10,
        git_runner=runner,
    )
    assert stale is None


# --- load_cached_document / save_cached_document -------------------------------------


def make_status(*, now: datetime = T0, last_success_at: datetime | None = T0, details: dict | None = None):
    return collector.ops_status.build_status(
        component="github",
        observed_at=now,
        last_success_at=last_success_at,
        healthy_max_age_seconds=5400,
        stale_max_age_seconds=21600,
        details=details or {"open_pr_count": 3},
        now=now,
    )


def test_save_and_load_cached_document_roundtrip(tmp_path):
    cache_path = tmp_path / "cache.json"
    status = make_status()
    collector.save_cached_document(cache_path, collector.ops_status.to_json_dict(status))

    loaded = collector.load_cached_document(cache_path)
    assert loaded is not None
    assert loaded["component"] == "github"
    assert loaded["details"]["open_pr_count"] == 3


def test_load_cached_document_none_when_missing(tmp_path):
    assert collector.load_cached_document(tmp_path / "missing.json") is None


def test_load_cached_document_none_when_invalid_json(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("not json", encoding="utf-8")
    assert collector.load_cached_document(cache_path) is None


def test_load_cached_document_none_when_contract_invalid(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({"component": "github"}), encoding="utf-8")
    assert collector.load_cached_document(cache_path) is None


def test_save_cached_document_never_raises_on_bad_path():
    # A path under a file (not a directory) can never be mkdir'd into; this must
    # degrade silently rather than raise, since caching is a resilience aid only.
    bad_path = Path("/dev/null/impossible/cache.json")
    collector.save_cached_document(bad_path, {"component": "github"})


# --- build_github_status: ComponentStatus state transitions ---------------------------


def make_facts(
    *,
    now: datetime = T0,
    prs: list[dict] | None = None,
    pr_limit: int = 50,
    pr_error_kind: str | None = None,
    pr_error_detail: str = "",
    branch_protection_known: bool = True,
    stale_branches: tuple[str, ...] | None = (),
    cached_document: dict | None = None,
) -> "collector.GithubFacts":
    return collector.GithubFacts(
        now=now,
        prs=prs,
        pr_limit=pr_limit,
        pr_error_kind=pr_error_kind,
        pr_error_detail=pr_error_detail,
        branch_protection_known=branch_protection_known,
        stale_branches=stale_branches,
        cached_document=cached_document,
    )


HEALTHY_KWARGS = dict(healthy_max_age_seconds=5400, stale_max_age_seconds=21600)


def test_build_status_healthy_on_fresh_success():
    prs = json.loads(OPEN_PRS_FIXTURE)
    facts = make_facts(prs=prs)
    status = collector.build_github_status(facts, **HEALTHY_KWARGS)

    assert status.state == "healthy"
    assert status.component == "github"
    assert status.last_success_at == T0
    assert status.details["open_pr_count"] == 5


def test_build_status_unknown_when_no_success_and_no_cache():
    facts = make_facts(prs=None, pr_error_kind="auth_error")
    status = collector.build_github_status(facts, **HEALTHY_KWARGS)

    assert status.state == "unknown"
    assert status.age_seconds is None
    assert status.details["last_error_kind"] == "auth_error"


def test_build_status_falls_back_to_cache_when_live_fetch_fails():
    cached_status = make_status(last_success_at=datetime(2026, 9, 11, 11, 30, 0, tzinfo=timezone.utc))
    cached_document = collector.ops_status.to_json_dict(cached_status)
    facts = make_facts(prs=None, pr_error_kind="network_error", cached_document=cached_document)

    status = collector.build_github_status(facts, **HEALTHY_KWARGS)

    # 30 minutes old, within the 5400s healthy window -- cache fallback still reads
    # as healthy rather than collapsing straight to unknown on one transient failure.
    assert status.state == "healthy"
    assert status.last_success_at == datetime(2026, 9, 11, 11, 30, 0, tzinfo=timezone.utc)
    assert status.details["open_pr_count"] == 3
    assert status.details["last_error_kind"] == "network_error"


def test_build_status_cache_fallback_degrades_with_age():
    old_success = datetime(2026, 9, 11, 5, 0, 0, tzinfo=timezone.utc)  # 7h before T0, past the 21600s stale bound
    cached_status = make_status(now=old_success, last_success_at=old_success)
    cached_document = collector.ops_status.to_json_dict(cached_status)
    facts = make_facts(prs=None, pr_error_kind="network_error", cached_document=cached_document)

    status = collector.build_github_status(facts, **HEALTHY_KWARGS)

    assert status.state == "stale"


def test_build_status_document_is_contract_valid():
    prs = json.loads(OPEN_PRS_FIXTURE)
    facts = make_facts(prs=prs)
    status = collector.build_github_status(facts, **HEALTHY_KWARGS)

    collector.ops_status.validate_component_status(status)
    document = collector.ops_status.to_json_dict(status)
    assert document["component"] == "github"


# --- collect_github_status: end-to-end with caching -----------------------------------


def test_collect_github_status_writes_cache_on_success(tmp_path):
    cache_path = tmp_path / "cache.json"
    git_runner = runner_from(
        {
            "git -C /repo remote get-url origin": FakeCompletedProcess(
                0, stdout="https://github.com/TripAndCode/transit-app.git\n"
            ),
            "git -C /repo for-each-ref": FakeCompletedProcess(0, stdout=""),
        }
    )
    gh_runner = runner_from(
        {
            "gh pr list": FakeCompletedProcess(0, stdout=OPEN_PRS_FIXTURE),
            "gh api": FakeCompletedProcess(0, stdout=BRANCHES_FIXTURE),
        }
    )

    status = collector.collect_github_status(
        repo=Path("/repo"),
        cache_path=cache_path,
        now=T0,
        gh_runner=gh_runner,
        git_runner=git_runner,
    )

    assert status.state == "healthy"
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text())
    assert cached["details"]["open_pr_count"] == 5


def test_collect_github_status_does_not_overwrite_cache_on_failure(tmp_path):
    cache_path = tmp_path / "cache.json"
    original_status = make_status(last_success_at=T0)
    collector.save_cached_document(cache_path, collector.ops_status.to_json_dict(original_status))
    original_mtime = cache_path.stat().st_mtime_ns

    git_runner = runner_from(
        {
            "git -C /repo remote get-url origin": FakeCompletedProcess(
                0, stdout="https://github.com/TripAndCode/transit-app.git\n"
            ),
            "git -C /repo for-each-ref": FakeCompletedProcess(0, stdout=""),
        }
    )
    gh_runner = runner_from(
        {
            "gh pr list": FakeCompletedProcess(1, stderr="gh: Bad credentials (HTTP 401)"),
            "gh api": FakeCompletedProcess(0, stdout=BRANCHES_FIXTURE),
        }
    )

    status = collector.collect_github_status(
        repo=Path("/repo"),
        cache_path=cache_path,
        now=T0,
        gh_runner=gh_runner,
        git_runner=git_runner,
    )

    assert status.details["last_error_kind"] == "auth_error"
    assert cache_path.stat().st_mtime_ns == original_mtime


def test_collect_github_status_unknown_when_not_a_git_repo(tmp_path):
    git_runner = runner_from(
        {
            f"git -C {tmp_path} remote get-url origin": FakeCompletedProcess(128, stderr=""),
            f"git -C {tmp_path} for-each-ref": FakeCompletedProcess(128, stderr="not a repo"),
        }
    )

    status = collector.collect_github_status(
        repo=tmp_path,
        cache_path=None,
        now=T0,
        git_runner=git_runner,
        gh_runner=failing_runner(AssertionError("gh should not be called without a resolved repo slug")),
    )

    assert status.state == "unknown"
    assert status.details["last_error_kind"] == "repo_slug_unresolved"


# --- CLI -----------------------------------------------------------------------------


def test_main_exit_code_1_and_unknown_state_offline(tmp_path, capsys):
    # tmp_path is not a git repo, so repo_slug resolution fails locally with no
    # network call attempted at all -- this is the CLI's only fully hermetic path.
    exit_code = collector.main(["--repo", str(tmp_path)])

    payload = capsys.readouterr().out
    assert exit_code == 1
    assert '"component": "github"' in payload
    assert '"state": "unknown"' in payload
    assert "repo_slug_unresolved" in payload


def test_main_writes_out_file(tmp_path):
    out_path = tmp_path / "status.json"
    collector.main(["--repo", str(tmp_path), "--out", str(out_path)])

    document = json.loads(out_path.read_text())
    assert document["component"] == "github"
