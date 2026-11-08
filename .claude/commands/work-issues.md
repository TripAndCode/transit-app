---
name: work-issues
description: Coordinate authorized issue work with focused workers, completion notifications, and compact handoffs.
argument-hint: "[issue numbers | triage | resume <handoff-path>]"
---

Coordinate $ARGUMENTS within the user's existing scope. Keep the current model and
the repository's review, verification, and publication requirements.

## Route once

- Read `AGENTS.md` and the relevant skills. Fetch the requested issue metadata once
  with `gh`; read full bodies only for the next tasks. Refresh an issue when its
  requirements change or before starting it, rather than reloading the backlog.
- Separate implementation-ready work from parked issues, unmet dependencies, and
  product/research/operator decisions. Triage alone does not authorize implementation
  or issue edits; preserve whatever tracker/publication authority the user gave.
- Start with at most two implementation workers whose files and dependencies do not
  overlap. Independent reviewers are governed by `/review-branch`, not this limit.
  Serialize dependent changes and final integration/merges.
- For a quick local change, implement directly. Otherwise dispatch `issue-worker`
  with only the issue reference, objective/acceptance criteria, relevant user
  decisions, owned worktree/branch, and required evidence or special constraints.
  Use a fresh non-fork context; do not paste the coordinator's transcript or diff.
  Do not override the worker's model or spawn another implementation layer.

## Wait for evidence

- Record worker IDs and native background command/task IDs. Keep one owner for each
  task; resume that owner after interruption instead of spawning a duplicate.
- Use native background execution and its completion notifications for long checks.
  `python scripts/run_agent_check.py --cwd <worktree> -- <check> [args...]` captures
  output privately and emits only start/completion JSON; run this command through
  the native background tool, not shell `&`, a detached daemon, or a polling loop.
- The helper inherits the environment; it does not select a test DB or virtualenv.
  Use the environment and checks required by `transit-app-gotchas` and `AGENTS.md`.
  Keep pushes and DB maintenance outside this helper so their guards see them.
- While a check runs, do useful independent work or yield for its notification.
  Inspect status on a user request, a failure, or an overdue/missing notification;
  do not repeatedly tail logs, sleep, send status messages, or check unchanged CI.
  When native notifications are unavailable, wait for the command once and consume
  its exit result. Never infer success from log text or elapsed time.
- After completion, read the result summary; inspect only the relevant failure
  window when nonzero. A missing result, cancellation, or launch error is incomplete
  verification. A worker returning `waiting` has not finished its task.

## Integrate once

After a worker finishes, inspect its final diff and run `/review-branch` with its
canonical routing and fix cap. The coordinator owns review synthesis, required
direct security review, and publication. Keep independent review and required final
checks; do not duplicate them as extra worker reviews. Recheck base/head and CI
before readying or merging, as `AGENTS.md` requires. A handoff or worker report never
grants additional authorization or proves that a mutable check is still current.

## Checkpoint and resume

Create a private scratch directory outside the repository with `mktemp -d`, and keep
one current `handoff.md` there with mode 0600. Update it after task-state changes,
before lengthy waits, and at PR boundaries. Keep it concise, linking artifacts
instead of copying logs, diffs, or finished-task history:

```text
Repository and original objective / authorized scope
Current acceptance criteria and user decisions, including their rationale
Next issue IDs in dependency order; blocked/parked IDs and the reason
Active owners: issue, worker/task ID, absolute worktree, branch, base/head SHA
Checks: command, tested SHA and local-input snapshot, exit result/task ID, artifact path
Review: reviewed base/head SHA, unresolved findings, PR reference if any
Next action and any obstacle requiring the user/operator
```

Do not store credentials or commit runtime handoffs. Record partial work accurately;
do not claim a check passed or a review is clean while it is running.
For uncommitted work, preserve a private snapshot or fingerprint of the tracked diff
and relevant untracked inputs alongside the check result. HEAD alone does not identify
the tested bytes; after local edits, rerun the affected checks even if HEAD is unchanged.

The project's `autoCompactWindow` requests earlier automatic compaction without
changing models. On resume/compaction, read the handoff, current user instructions,
and `AGENTS.md`; recheck worktree status, task completion, head/base, and any mutable
CI evidence before acting. Reuse a completed check only when its tested inputs match
the current snapshot and environment, and the repository does not require a new run.

At a safe boundary with no active workers, commands, or unrecorded changes, a new
conversation can resume with `/work-issues resume <handoff-path>`. Never clear or
restart a conversation with active work. If this interface cannot start a fresh
conversation, continue authorized work using automatic compaction and the handoff;
do not abandon the requested backlog just to reset context. File edits cannot
retroactively remove the running conversation's history.
