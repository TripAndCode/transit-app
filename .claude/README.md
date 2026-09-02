# .claude/commands and .claude/agents

Slash commands + subagents for PR review workflow. Related: `.claude/skills/`
(background knowledge, invoked automatically) vs these two dirs (workflows you
trigger explicitly).

## Commands (`.claude/commands/*.md`)

Invoke as `/name` from a Claude Code session. Two lifecycles: work on **your own**
branch/PR, or review **a PR you did not author** — including one `/vps-loop-run`
opened on a `vps-loop/item-*` branch. Pick the command by whose code it touches and
what stage you are at.

### Your own work

| Command | Does | Reads/writes |
|---|---|---|
| `/review-branch` | Builds one secret-aware diff + JSON manifest, then uses two complementary reviewers for normal changes. Process docs use one; enforcement adds one; high-risk changes receive one final integrated pass. Clean groups are never repeated just for “fresh eyes.” | Read-only + proportional checks. No commit/push. |
| `/pr-github` | Posts chosen `/review-branch` findings as inline `gh` comments on the PR. Also defines PR-description style (scannable, table-first, bold keywords). | Writes to GitHub via `gh`. |
| `/vps-loop-run` | Coordinator for one autonomous VPS-loop tick: state check → sync `main` → pick one item → isolated worker → two full independent `/review-branch` passes → PR marked ready → squash-merge → `/cleanup-merged`, all gated on both passes being clean and the PR reporting mergeable/clean. | Reads `NEXT_TASK.md`, appends its Status log; pushes feature branches, opens/readies/merges PRs via `gh`. |
| `/cleanup-merged` | Post-merge maintenance: syncs `main`, runs an evidence-based dry run, removes only proven-stale local branches/worktrees, and can repeat on a VPS clone. | Deletes clean local refs/worktrees only; never deletes GitHub branches or files. |
| `/address-my-pr-comments` | Pulls unresolved review threads on your own PR (REST + GraphQL for resolve-state), judges each vs current code, drafts replies/fixes, **waits for per-thread approval** before posting or editing anything. Never resolves threads itself. | Reads via `gh`; writes only after explicit approval. |

Typical flow: `/review-branch` → `/pr-github` (post findings) → reviewer replies
→ `/address-my-pr-comments` (triage + fix + reply) → merge → `/cleanup-merged`.

### A PR you did not author

| Command | Does | Reads/writes |
|---|---|---|
| `/review-pr` | Fetches the PR head into `.worktrees/review-<branch>`, builds the diff and manifest with `scripts/prepare_review.py`, then applies `/review-branch`'s exact routing. Deduplicates against threads already on the PR and reports in the terminal. | Read-only on code. Posts nothing, writes no report file, unless asked. |
| `/follow-up-pr-review` | Judges the threads **you** opened once the author replied or pushed (`settled` reported only; `discuss` gets a drafted reply behind a per-item gate), and scans `old_head..new_head` since your last look for regressions, staleness, and refactor opportunities. Never resolves a thread. | Read-only on code; posts replies and new threads only after per-item approval. |

Typical flow: `/review-pr <n>` → discuss findings (optionally `/pr-github` to post
them) → author replies or pushes → `/follow-up-pr-review <n>`.

`/follow-up-pr-review`'s delta scan takes its baseline from the `/review-pr` worktree's
current head, so removing that worktree removes the baseline; the next run then skips
the scan instead of re-reviewing the whole PR. `.worktrees/` is gitignored.

The split by whose PR it is matters because only your own branch can be fixed in place:
`/address-my-pr-comments` may apply code changes, `/follow-up-pr-review` may not.
Neither ever resolves a thread — that is always a manual step in the GitHub UI.

`scripts/prepare_review.py` is the deterministic front end for `/review-branch`,
`/review-pr`, and `/follow-up-pr-review`'s delta scan. It produces the private diff
and JSON routing manifest, so commands should consume its output rather than
reimplementing path exclusions, line counts, or test-share math.
`scripts/cleanup_git_state.py` is the deletion authority for `/cleanup-merged` and the
VPS loop; it defaults to dry-run and rechecks mutable state before applying a plan.
`scripts/comment_lint.py` narrows the `comments` dimension to comments left unchanged
beside changed code, and its gate mode rejects banners, over-long blocks, pointers at
other comments, and line-number references. It reads Python and TypeScript only, so it
has nothing to say about a Markdown-only diff. All three are versioned here on purpose:
a review rule that lives in one machine's home directory is not a review rule the VPS
loop or a second checkout can apply.

Each command file states its own token-frugality rules inline, in the phase they apply
to. This README is a map, not a rule store: no command loads it, so nothing here is
enforceable — treat every line above as a possibly-stale summary of the command file it
describes, never as the rule itself.

## Agents (`.claude/agents/*.md`)

| Agent | Role | Tools |
|---|---|---|
| `branch-reviewer` | Focused reviewer for one merged group of dimensions. It reads the prepared diff once and uses targeted evidence gathering. `.claude/agents/branch-reviewer.md` is the dimension source of truth. | Read, Grep, Glob, Bash (model: sonnet) |

Dimensions as of this writing: `bugs`, `logic`, `consistency`, `perf`, `practices`,
`comments`, `security`, `alternatives`, plus `enforcement` (conditional — lint/CI/hook
diffs only) — this list is a convenience snapshot and can drift; check the agent file
for the current list and exact scope of
each (e.g. `security` covers hardcoded creds, CSRF/SSRF, PII/PDPA-APPI,
session-cookie flags; `consistency` covers cross-file contract drift like
i18n key parity or `agg_*` column renames; `comments` narrows to the stale-candidate
list from `scripts/comment_lint.py` and enforces `CLAUDE.md`'s durable-content rule).

## Guardrails baked into these files

- DB safety: any SQL is read-only against the dev Postgres and dev ClickHouse;
  tests point at throwaway `transit_test`@5544 and ClickHouse @8124.
  `hooks/guard-dev-db.sh` is a partial net, not a guarantee: it only fires when the
  command text *literally* names the dev port or container AND carries a write/DDL
  keyword. An env-var write, a Makefile target, or a Python script reading
  `DATABASE_URL` is not caught, and ClickHouse isn't covered at all. It also
  false-positives on prose that merely mentions those names. Treat the rule in
  `CLAUDE.md` as the protection, not the hook.
- No command here commits or pushes without explicit user go-ahead, except
  `/vps-loop-run`, which runs unattended: it may push feature branches, open,
  ready, and squash-merge PRs once both required `/review-branch` passes are
  clean and the PR reports mergeable/clean — but it never pushes directly to
  `main` (only via a reviewed, merged PR) and never force-pushes.
- Neither `/address-my-pr-comments` nor `/follow-up-pr-review` calls the GraphQL
  `resolveReviewThread` mutation — resolving is always a manual step in the GitHub UI.
- Reviewers dispatched for one diff read a single worktree concurrently, so the agent
  file forbids `git checkout`/`switch`/`reset` inside it and requires
  `git show <rev>:<path>` for other revisions.

## VPS operations

- Cron invokes a short `claude -p "/vps-loop-run"` wrapper; the command file owns
  orchestration. `NEXT_TASK.md` is local/untracked and missing or empty means no-op.
  `/root/claude-loop.sh` itself is VPS-local infrastructure, not tracked in this
  repo -- it runs the invocation with `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`
  so a dispatched Step-4 worker's background Agent task isn't killed by the
  CLI's default ~600s wait ceiling: `claude -p` is one-shot, so a "you'll be
  notified when it finishes" expectation after that ceiling can never be
  fulfilled, and the still-in-progress worker's uncommitted edits are lost
  when the parent process exits. A genuinely-hung worker is still caught by
  `vps-heartbeat-watchdog.yml`, since a stuck run never reaches the
  heartbeat line either.
- Non-interactive SSH and cron shells do not source `~/.bashrc`. Put required OAuth
  variables in `/etc/environment` and expose binaries through `/usr/local/bin`.
- To trigger early, SSH to the VPS and run `/root/claude-loop.sh`; otherwise wait for
  cron. The loop operates on one item per tick and may merge its own PR once
  both review passes are clean and it's mergeable/clean.
- The pre-push backend timeout is 420 seconds — the full suite's legitimate
  wall-clock time leaves real headroom on a small VPS, which can run
  noticeably slower than a typical dev machine. A timeout with no test
  failure is an infrastructure limitation, not evidence that tests failed;
  resolve it before weakening the gate.
- An hourly crontab entry (`15 * * * *`, JST — the VPS's system timezone —
  distinct from `/vps-loop-run`'s own cron cadence — see `crontab -l` for the
  current interval, not a hardcoded figure here — and from the Oracle
  collector VM's 9am-JST jobs on a different machine) runs
  `python3 /root/transit-app/scripts/daily_git_hygiene.py --apply`,
  appending to `/root/git-hygiene.log`. The script itself only performs its
  real cleanup once per calendar day (tracked via a same-day completion
  marker, default `/root/.daily_git_hygiene_last_success`) — the trigger is
  hourly specifically so that losing the `/tmp/claude-loop.lock` race
  against a live `/vps-loop-run` tick costs an hourly retry instead of
  waiting a full day for the next attempt. It closes gaps `/vps-loop-run` either
  only handles reactively or never handles at all: local worktree/branch
  cleanup (handled reactively as a tick side effect, but not during long
  idle stretches or a stuck loop), merged `vps-loop/item-*` branches piling
  up unbounded on GitHub (never deleted by the loop itself), and stale local
  `-superseded-<sha>` backup branches (created by Step 2b, never pushed —
  no automated prune path existed at all). Like `/root/claude-loop.sh`, the
  crontab wiring itself is VPS-local infrastructure, not tracked in this
  repo — only the
  script it invokes is.
