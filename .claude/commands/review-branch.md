---
name: review-branch
description: Token-bounded review of the current branch vs main, followed by proportional verification.
---

Review the current branch for project $ARGUMENTS. Optimize for evidence per token:
normal changes get two complementary reviewers and one pass; extra calls require a
specific risk signal or a material fix.

## 1. Prepare once

1. Run `git status --porcelain` and inspect changed **path names only** for an
   unexpected credential-bearing file. Never print its contents.
2. Create a private scratch directory and run:
   ```bash
   python3 scripts/prepare_review.py \
     --repo <worktree-absolute-path> --base main --output-dir <scratch-directory> \
     > <scratch-directory>/manifest.json
   ```
   If step 1 found an additional sensitive path, add `--exclude '<path-or-glob>'` to
   this **first** invocation; never create an unfiltered artifact and clean it up
   afterward. Treat `manifest.json` as canonical; do not recalculate its file list,
   line counts, exclusions, or test share in prose.
3. Confirm the manifest's `head` still equals the reviewed worktree's `HEAD`. Deduce
   the objective and state it in one short paragraph.

The script writes committed, untracked, unstaged, and staged changes into one
mode-0600 diff without serializing known lockfiles or credential carriers.

## 2. Route the review

Use the manifest's path-only tier as a suggestion and correct it when semantics show
otherwise:

- **Trivial:** human-facing Markdown outside `.claude/**` and root `CLAUDE.md`, with
  no executable instructions. Review directly; no subagent.
- **Process-doc:** only `.claude/**` and/or root `CLAUDE.md`. Dispatch one
  `branch-reviewer` for `logic+consistency+practices+comments+security`. If
  `enforcement` is true, add one standalone `enforcement` call.
- **Standard:** dispatch exactly two `branch-reviewer` calls:
  1. `bugs+logic+consistency+security`
  2. `perf+practices+comments+alternatives`
  Add one standalone `enforcement` call only when the manifest flag is true and the
  diff actually changes a quality gate.

A process-doc diff is Markdown, which `comment_lint.py` does not read, so `comments`
runs there on its empty-list fallback. The agent file owns what that fallback is.

**High-risk overlay:** auth/session/admin authorization, credential or PII handling,
user-supplied URLs, schema/data migrations, destructive data paths, or security
controls. For these diffs, keep the total at three calls by splitting `security` from
the first group; merge `security+enforcement` when both apply.

Every dispatch receives only: manifest path, diff path, objective, assigned
dimensions, worktree path, and this exact line:

`Deliberately excluded, do NOT re-derive: <manifest paths>. This is not truncation.`

A dispatch whose assigned dimensions include `comments` additionally receives the
manifest's `merge_base`, which that dimension needs to build its stale-candidate list.
No other dimension takes a base ref.

Do not paste diff text into prompts. Reviewers never receive one another's output.
While they run, do not switch, reset, or otherwise move the reviewed worktree; the
agent file forbids reviewers from moving it for the same reason.

## 3. Synthesize and iterate only when needed

Deduplicate findings and keep evidence-backed Major/Minor issues. If a Major is
fixed, regenerate the manifest and rerun only the reviewer group that owned that
finding. Cap at two fix iterations. Do not repeat clean groups merely for “fresh
eyes.”

For a high-risk diff, after all Major findings are resolved, run one final integrated
review over the cumulative diff. Use Opus for this final call when the Agent tool
supports a model override; otherwise use the configured reviewer. A third full read
is justified only when that final review itself caused a material code change.

If a known PR number was supplied, fetch its review threads once. Suppress a finding
only when the same location is already raised and the current code demonstrably fixes
it; otherwise mark it as already raised and keep it active.

## 4. Verify once

- Check that new logic has a concrete matching test; use the manifest's `test_share`
  only as a prompt to inspect, never as a finding by itself.
- Remove dead code or misleading comments introduced by the diff.
- Run the smallest relevant checks, followed by the repository-required final check.
  Tests must use Postgres `:5544` and ClickHouse `:8124`, never the dev databases.
- Capture verbose command output in the scratch directory. Report command, exit code,
  and a short success summary; on failure, read only the useful tail and debug before
  claiming completion.
- Invoke `superpowers:verification-before-completion` if available. Invoke
  `systematic-debugging` only after a check fails; load `postgres-perf` or
  `maplibre-map` only when the diff touches their domains.

## Boundaries

- Do not commit or push.
- In a worktree, run every Git command with `git -C <worktree-absolute-path>` and
  confirm `HEAD` before and after reviewer calls.
