"""Regression coverage for guard-push-quality.sh's command parser.

The hook's own resolution logic (which directory a `git push` actually
comes from) has repeatedly needed a new heuristic for a command shape
manual review missed -- most recently a multi-line command silently
collapsing into one unsplit statement, and a `src:dst` refspec resolving
the wrong half. These tests pin the parser's behavior for the shapes
those bugs came from so a regression fails a test run instead of waiting
for the next review pass.

What runs here is extracted verbatim from the hook -- the embedded Python
parser, and the bash that decides whether a failing dead-code check is a
real finding or a toolchain that could not start -- so these tests cannot
silently drift out of sync with what ships. The script is never run end to
end: the rest of it depends on a real git worktree and a provisioned
poetry/npm environment, which is integration-test territory this file
deliberately stays out of.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".claude" / "hooks" / "guard-push-quality.sh"

_START_MARKER = "python3 -c '\nimport json, shlex, sys"
_END_MARKER = "\n' 2>/dev/null\n)\""


def _embedded_parser_source() -> str:
    text = HOOK_PATH.read_text()
    start = text.index(_START_MARKER) + len("python3 -c '\n")
    end = text.index(_END_MARKER, start)
    return text[start:end]


PARSER_SOURCE = _embedded_parser_source()


def _parse(command: str) -> dict:
    payload = json.dumps({"tool_input": {"command": command}})
    result = subprocess.run(
        ["python3", "-c", PARSER_SOURCE],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_simple_push_captures_ref():
    parsed = _parse("git push origin fix/push-gate-worktree-scope")
    assert parsed["refs"] == ["fix/push-gate-worktree-scope"]
    assert parsed["dir"] == ""
    assert parsed["cd_dir"] == ""


def test_dash_c_captures_directory():
    parsed = _parse("git -C /some/worktree push origin somebranch")
    assert parsed["dir"] == "/some/worktree"
    assert parsed["refs"] == ["somebranch"]


def test_multiline_cd_then_push_is_not_collapsed_into_one_statement():
    """The bug: shlex.split() treats a bare newline as ordinary whitespace,
    so a `cd` line followed by a `git push` line on separate physical lines
    used to merge into a single statement, and the `cd`-capture heuristic
    (which only recognizes a statement whose *first* token is literally
    "cd") never fired."""
    command = "echo starting deploy\ncd /Users/example/worktree\ngit push origin somebranch"
    parsed = _parse(command)
    assert parsed["cd_dir"] == "/Users/example/worktree"
    assert parsed["refs"] == ["somebranch"]


def test_multiline_with_preceding_unrelated_statement_still_resolves_cd():
    command = "git worktree add /tmp/x\ncd /tmp/x\ngit push origin feature"
    parsed = _parse(command)
    assert parsed["cd_dir"] == "/tmp/x"
    assert parsed["refs"] == ["feature"]


def test_backslash_newline_line_continuation_does_not_corrupt_the_next_token():
    """The bug: an unquoted backslash-newline is a real shell line
    continuation (both characters vanish, joining the two physical lines),
    but a naive newline-to-separator rewrite left the backslash in place
    and shlex.split() then kept the following newline as a literal
    character glued onto the next token instead of eliding the pair --
    corrupting cd_dir with an embedded newline rather than a clean path."""
    command = "cd \\\n/some/worktree\ngit push origin somebranch"
    parsed = _parse(command)
    assert parsed["cd_dir"] == "/some/worktree"
    assert parsed["refs"] == ["somebranch"]


def test_newline_inside_a_quoted_string_is_not_treated_as_a_separator():
    """A multi-line commit message is ordinary content, not a statement
    boundary -- only a newline outside quotes splits statements."""
    command = 'git commit -m "line one\nline two"\ngit push origin somebranch'
    parsed = _parse(command)
    assert parsed["refs"] == ["somebranch"]


def test_bare_push_has_no_directory_or_ref_information():
    parsed = _parse("git push")
    assert parsed == {"dir": "", "cd_dir": "", "refs": [], "is_delete": False}


def test_push_origin_head_captures_head_literally():
    parsed = _parse("git push origin HEAD")
    assert parsed["refs"] == ["HEAD"]


def test_explicit_refspec_is_captured_whole_for_bash_side_splitting():
    parsed = _parse("git push origin local-feature:renamed-remote-branch")
    assert parsed["refs"] == ["local-feature:renamed-remote-branch"]


def test_delete_refspec_sets_is_delete():
    parsed = _parse("git push origin :old-branch")
    assert parsed["is_delete"] is True


def test_and_and_separator_still_splits_statements():
    parsed = _parse("cd /tmp/worktree && git push origin somebranch")
    assert parsed["cd_dir"] == "/tmp/worktree"
    assert parsed["refs"] == ["somebranch"]


def test_later_cd_overrides_an_earlier_one():
    parsed = _parse("cd /tmp/a\ncd /tmp/b\ngit push origin somebranch")
    assert parsed["cd_dir"] == "/tmp/b"


def _bash_refspec_source_half(ref: str) -> str:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'ref="$1"; branch="${ref%%:*}"; branch="${branch#refs/heads/}"; printf "%s" "$branch"',
            "--",
            ref,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_refspec_source_half_extraction_takes_the_local_branch_not_the_remote_name():
    """The bug: `${ref##*:}` (destination half) was used where the source
    half -- the actual local branch being pushed -- was needed, so an
    explicit `src:dst` refspec resolved to a remote-side name that
    typically isn't a local branch/worktree at all."""
    assert _bash_refspec_source_half("local-feature:renamed-remote-branch") == "local-feature"
    assert _bash_refspec_source_half("refs/heads/local-feature:refs/heads/renamed") == "local-feature"
    assert _bash_refspec_source_half("plain-branch") == "plain-branch"


def _frontend_gate_block() -> str:
    """The RUN_FRONTEND block: everything between its opening `if` and the
    matching `fi` that closes it, right before the final `if [ "$FAIL" -ne 0
    ]` gate."""
    text = HOOK_PATH.read_text()
    start = text.index('if [ "$RUN_FRONTEND" -eq 1 ]; then')
    end = text.index('if [ "$FAIL" -ne 0 ]; then\n  echo "BLOCKED: git push — quality gate failed', start)
    return text[start:end]


def test_frontend_gate_runs_every_check_ci_runs():
    """The local push gate must not silently omit a check CI enforces --
    CI added deadcode, the CSS-tokens checker's own unit tests, and the
    CSS-tokens static scan (frontend/.github/workflows/ci.yml), and this
    gate is the only local signal for a push made from a worktree (see the
    module docstring in the hook itself)."""
    block = _frontend_gate_block()
    for script in ("npm run deadcode", "npm run test:check-css-tokens", "npm run check:css-tokens"):
        assert script in block, f"{script} missing from the RUN_FRONTEND gate block"


def _deadcode_classifier() -> str:
    """The hook's `rc`-dispatch for the deadcode check, extracted verbatim so
    these tests exercise what ships rather than a restatement of it."""
    block = _frontend_gate_block()
    start = block.index('    if [ "$rc" -eq 124 ]; then')
    end = block.index("\n    fi\n", start) + len("\n    fi\n")
    return block[start:end]


def _deadcode_verdict(rc: int, stdout: str) -> int:
    """Run that dispatch with a given knip exit status and stdout, and report
    the FAIL it leaves behind. `run_with_timeout` is stubbed out: the plain
    re-run it performs exists only to put a reason in the log."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "deadcode_out"
        out.write_text(stdout)
        script = (
            "FAIL=0\n"
            f"rc={rc}\n"
            f'deadcode_out="{out}"\n'
            "run_with_timeout() { return 0; }\n"
            f"{_deadcode_classifier()}"
            'echo "FAIL=$FAIL"\n'
        )
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return int(result.stdout.strip().rsplit("FAIL=", 1)[1])


def test_a_knip_run_that_reported_findings_blocks_the_push():
    assert _deadcode_verdict(1, '{"issues":[{"file":"src/dead.ts"}]}') == 1


def test_a_toolchain_that_cannot_run_knip_warns_instead_of_blocking_every_push():
    """A knip that cannot start (Node outside its `engines` range,
    oxc-parser's native binary missing, the npm script renamed) exits
    non-zero exactly like one that found dead code, and this gate runs
    against the main checkout for every push in the repository -- so failing
    on a broken toolchain would block all of them at once. Only a run that
    produced a report may block."""
    assert _deadcode_verdict(1, "") == 0
    assert _deadcode_verdict(1, "Error [ERR_REQUIRE_ESM]: require() of ES Module ...\n") == 0


def test_a_deadcode_check_that_never_finished_still_blocks():
    """The warn path is a narrow exception for a classified failure. A
    watchdog kill classifies nothing, so the file header's fail-closed
    contract applies unchanged."""
    assert _deadcode_verdict(124, "") == 1


def test_a_clean_knip_run_blocks_nothing():
    assert _deadcode_verdict(0, '{"issues":[]}') == 0
