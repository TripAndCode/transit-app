# .claude/commands and .claude/agents

Slash commands + subagents for PR review workflow. Related: `.claude/skills/`
(background knowledge, invoked automatically) vs these two dirs (workflows you
trigger explicitly).

## Commands (`.claude/commands/*.md`)

Invoke as `/name` from a Claude Code session. Two lifecycles: work on **your own**
branch/PR, or review **a PR you did not author**. Pick the command by whose code it touches and
what stage you are at.

### Your own work

| Command | Does | Reads/writes |
|---|---|---|
| `/review-branch` | Builds one secret-aware diff + JSON manifest, then uses two complementary reviewers for normal changes. Process docs use one; enforcement adds one; high-risk changes receive one final integrated pass. Clean groups are never repeated just for “fresh eyes.” | Read-only + proportional checks. No commit/push. |
| `/pr-github` | Posts chosen `/review-branch` findings as inline `gh` comments on the PR. Also defines PR-description style (scannable, table-first, bold keywords). | Writes to GitHub via `gh`. |
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
`scripts/cleanup_git_state.py` is the deletion authority for `/cleanup-merged` and
`scripts/daily_git_hygiene.py`; it defaults to dry-run and rechecks mutable state before applying a plan.
`scripts/comment_lint.py` narrows the `comments` dimension twice over: unchanged
comments beside changed code via `--stale-candidates`, and banners, over-long blocks,
pointers at other comments, and line-number references the diff introduced via
`--diff --warn`. A warn-only pre-push hook outside this repository runs the
same linter; it never blocks and never runs during review, so that dimension is the
only place those rules are actually applied — `--warn` reports without gating. `--baseline` sweeps every tracked
source for the same rules, for a deliberate repository-wide pass rather than a review.
It reads Python, TypeScript, and JavaScript only, so it has nothing to say about a
Markdown-only diff. All three scripts are versioned here on purpose: a review rule
living in one machine's home directory is not a rule a second checkout can apply.

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
  `hooks/guard-dev-db.sh` (a thin wrapper around `hooks/guard_dev_db.py`) is a
  partial net, not a guarantee: it shlex-tokenizes the command and blocks only
  when a dev-store target — a dev Postgres/ClickHouse port or container name,
  `docker compose exec`/`run` against the dev service, or a `migrate-down`/
  `db-reset` Make target with no throwaway port in the same command — appears
  alongside a write/DDL keyword, or a `psql -f`/`--file` invocation whose
  script contents it can't read. It has no visibility into a script's
  contents beyond that, or into a `DATABASE_URL` set outside the command line
  it sees, and it deliberately still blocks prose that merely names a dev
  store next to a write-sounding word — a false block only costs a rephrase,
  a missed write costs the dataset. Treat the rule in `CLAUDE.md` as the
  protection, not the hook.
- No command here commits or pushes without explicit user go-ahead.
- Neither `/address-my-pr-comments` nor `/follow-up-pr-review` calls the GraphQL
  `resolveReviewThread` mutation — resolving is always a manual step in the GitHub UI.
- Reviewers dispatched for one diff read a single worktree concurrently, so the agent
  file forbids `git checkout`/`switch`/`reset` inside it and requires
  `git show <rev>:<path>` for other revisions.

## VPS operations

- Provisioning the VPS's persistent clone must include one `make hooks` (or
  `make bootstrap`) run, which installs the pinned, mandatory gitleaks
  pre-commit hook via `scripts/setup_git_hooks.sh` — it fails loudly rather
  than silently skip if gitleaks/pre-commit can't be installed. `git
  worktree`s share a single `.git/hooks` directory (it lives in the common
  git dir, not per-worktree), so this one run also covers every worktree
  cut from that clone afterward. `make doctor` reports whether the hook is
  currently installed.
- Shared hooks apply on the VPS too. VPS-only permissions live in the ignored
  `.claude/settings.local.json` and must never be committed.
- Non-interactive SSH and cron shells do not source `~/.bashrc`. Put required OAuth
  variables in `/etc/environment` and expose binaries through `/usr/local/bin`.
- The pre-push backend timeout lives in `.claude/hooks/guard-push-quality.sh`
  (read the ceiling there, not a hardcoded figure here). It is sized to clear
  the full suite's legitimate wall-clock with headroom, including on a small
  VPS, which can run noticeably slower than a typical dev machine; that
  script's own `.claude/settings.json` entry bounds the sum of every ceiling
  in it. A timeout with no test failure is an infrastructure limitation, not
  evidence that tests failed; resolve it before weakening the gate.
- An hourly crontab entry (`15 * * * *`, JST — the VPS's system timezone; see
  `crontab -l` for the current interval) runs
  `python3 /root/transit-app/scripts/daily_git_hygiene.py --apply`, appending to
  `/root/git-hygiene.log`. The script performs its real cleanup at most once per
  calendar day (a same-day completion marker, default
  `/root/.daily_git_hygiene_last_success`); the trigger is hourly so a failed or
  lock-skipped run retries within the hour instead of waiting a full day. Two
  stages: local worktree/branch cleanup through `scripts/cleanup_git_state.py`,
  and orphaned poetry virtualenvs — poetry names a project's venv by hashing
  its absolute path, so every deleted worktree leaves a several-GB venv behind
  that poetry never revisits. This requires `poetry` on the cron shell's
  `PATH` (see the non-interactive-shell note above), and it assumes
  `/root/transit-app` is the only clone of this project on the host — an
  independent second clone's venv isn't detectable as in-use and would
  eventually be pruned. The crontab wiring itself is VPS-local installation
  state, not tracked in this repo — only the script it invokes is.
