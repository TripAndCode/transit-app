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
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".claude" / "hooks" / "guard-push-quality.sh"
SETTINGS_PATH = ROOT / ".claude" / "settings.json"

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
    """shlex.split() treats a bare newline as ordinary whitespace, so a
    `cd` line followed by a `git push` line on separate physical lines must
    still parse as two statements: merged into one, the `cd`-capture
    heuristic (which only recognizes a statement whose *first* token is
    literally "cd") would never fire."""
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
    """An unquoted backslash-newline is a real shell line continuation
    (both characters vanish, joining the two physical lines). A naive
    newline-to-separator rewrite would leave the backslash in place, and
    shlex.split() would then keep the following newline as a literal
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
    """An explicit `src:dst` refspec must yield its source half -- the
    actual local branch being pushed. The destination half (`${ref##*:}`)
    is a remote-side name that typically isn't a local branch/worktree at
    all."""
    assert _bash_refspec_source_half("local-feature:renamed-remote-branch") == "local-feature"
    assert _bash_refspec_source_half("refs/heads/local-feature:refs/heads/renamed") == "local-feature"
    assert _bash_refspec_source_half("plain-branch") == "plain-branch"


def _frontend_gate_block() -> str:
    """The RUN_FRONTEND block: everything between its opening `if` and the
    matching `fi` that closes it, right before the final `if [ "$FAIL" -ne 0
    ]` gate."""
    text = HOOK_PATH.read_text()
    start = text.index('if [ "$RUN_FRONTEND" -eq 1 ]; then')
    end = text.index('if [ "$FAIL" -ne 0 ]; then\n  block_with_log "quality gate failed"', start)
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


def _hook_entry_timeout() -> int:
    """The harness `timeout` on this hook's own `.claude/settings.json` entry."""

    def entries(node):
        if isinstance(node, dict):
            if "guard-push-quality.sh" in str(node.get("command", "")):
                yield node
            for value in node.values():
                yield from entries(value)
        elif isinstance(node, list):
            for value in node:
                yield from entries(value)

    found = list(entries(json.loads(SETTINGS_PATH.read_text())))
    assert len(found) == 1, "guard-push-quality.sh must be registered exactly once in .claude/settings.json"
    return int(found[0]["timeout"])


def _ceilings(hook_text: str) -> list[int]:
    return [int(n) for n in re.findall(r"run_with_timeout (\d+)", hook_text)]


def test_the_step_ceilings_fit_inside_the_hooks_own_timeout():
    """The harness kills the hook at its settings.json `timeout`, and whether
    a killed hook blocks the push is outside the script's control -- the one
    outcome its fail-closed design cannot guarantee. Every per-step ceiling
    together, the deadcode re-run included, must therefore stay under it."""
    ceilings = _ceilings(HOOK_PATH.read_text())
    assert ceilings, "no run_with_timeout ceilings found in the hook"
    assert sum(ceilings) < _hook_entry_timeout()


def test_the_ceiling_check_sees_a_ceiling_raised_past_the_hooks_timeout():
    """Positive control for the check above: the same reading of the hook,
    with only the backend ceiling raised by the whole harness timeout, must
    exceed it. A parser that stopped seeing a ceiling would pass the check
    above vacuously and fail here."""
    timeout = _hook_entry_timeout()
    text = HOOK_PATH.read_text()
    assert "run_with_timeout 1200 " in text
    raised = text.replace("run_with_timeout 1200 ", f"run_with_timeout {1200 + timeout} ", 1)
    assert sum(_ceilings(raised)) >= timeout


def _function_source(name: str) -> str:
    text = HOOK_PATH.read_text()
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + len("\n}\n")]


def _final_gate_source() -> str:
    text = HOOK_PATH.read_text()
    start = text.rindex('if [ "$FAIL" -ne 0 ]; then')
    return text[start : text.index("\nexit 0", start)]


def test_a_step_that_runs_out_of_time_is_named_when_the_push_is_blocked():
    """A timeout prints no error of its own, and the BLOCKED tail is whatever
    a later step printed last (usually the build's chunk table), so the step
    that actually ran out of time has to be named outright."""
    script = (
        'LOG="$(mktemp)"\n'
        'TIMED_OUT="$(mktemp)"\n'
        "MARKER_FILES=()\n"
        "FAIL=0\n"
        + _function_source("_run_with_watchdog")
        + _function_source("run_with_timeout")
        + _function_source("block_with_log")
        + "run_with_timeout 1 sleep 10 || FAIL=1\n"
        + 'echo "a later step that passed" >>"$LOG"\n'
        + _final_gate_source()
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "TIMED OUT after 1s: sleep 10" in result.stderr
