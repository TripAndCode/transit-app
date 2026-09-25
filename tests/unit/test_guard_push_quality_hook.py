"""Regression coverage for guard-push-quality.sh's command parser.

The hook's own resolution logic (which directory a `git push` actually
comes from) has repeatedly needed a new heuristic for a command shape
manual review missed -- most recently a multi-line command silently
collapsing into one unsplit statement, and a `src:dst` refspec resolving
the wrong half. These tests pin the parser's behavior for the shapes
those bugs came from so a regression fails a test run instead of waiting
for the next review pass.

Only the embedded Python parser is exercised here (extracted verbatim
from the hook file, so this can't silently drift out of sync with what
actually ships), not the full bash script end to end -- the rest of the
script's behavior depends on a real git worktree and a provisioned
poetry/npm environment, which is integration-test territory this file
deliberately stays out of.
"""

from __future__ import annotations

import json
import subprocess
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


def test_a_toolchain_that_cannot_run_knip_warns_instead_of_blocking_every_push():
    """A knip that cannot start (Node too old for its `engines`, oxc-parser's
    native binary missing) exits non-zero exactly like one that found dead
    code. This gate runs against the main checkout for every push in the
    repository, so failing on a broken toolchain would block all of them.
    The two are separated by whether a report came out, which is why the
    JSON reporter is what the gate runs -- so only a real finding blocks."""
    block = _frontend_gate_block()
    deadcode = block[block.index("npm run deadcode") : block.index("npm run test:check-entry-chunk")]
    assert "--reporter json" in deadcode, "the classifier needs a machine-readable report to key on"
    assert "JSON.parse" in deadcode
    parsed, _, unparsed = deadcode.partition("JSON.parse")
    assert "FAIL=1" not in parsed, "the deadcode check must not fail before it knows knip actually ran"
    assert "FAIL=1" in unparsed, "a knip run that produced a report must still block the push"
