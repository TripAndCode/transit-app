# .claude/commands and .claude/agents

Slash commands + subagents for PR review workflow. Related: `.claude/skills/`
(background knowledge, invoked automatically) vs these two dirs (workflows you
trigger explicitly).

## Commands (`.claude/commands/*.md`)

Invoke as `/name` from a Claude Code session.

| Command | Does | Reads/writes |
|---|---|---|
| `/review-branch` | Builds one secret-aware diff + JSON manifest, then uses two complementary reviewers for normal changes. Process docs use one; enforcement adds one; high-risk changes receive one final integrated pass. Clean groups are never repeated just for “fresh eyes.” | Read-only + proportional checks. No commit/push. |
| `/pr-github` | Posts chosen `/review-branch` findings as inline `gh` comments on the PR. Also defines PR-description style (scannable, table-first, bold keywords). | Writes to GitHub via `gh`. |
| `/vps-loop-run` | Coordinator for one autonomous VPS-loop tick: state check → sync `main` → pick one item → isolated worker → proportional `/review-branch` verification → draft PR. Never marks ready or merges. | Reads `NEXT_TASK.md`, appends its Status log; pushes feature branches + opens PRs via `gh`. |
| `/address-my-pr-comments` | Pulls unresolved review threads on your own PR (REST + GraphQL for resolve-state), judges each vs current code, drafts replies/fixes, **waits for per-thread approval** before posting or editing anything. Never resolves threads itself. | Reads via `gh`; writes only after explicit approval. |

Typical flow: `/review-branch` → `/pr-github` (post findings) → reviewer replies
→ `/address-my-pr-comments` (triage + fix + reply).

`scripts/prepare_review.py` is the deterministic front end for `/review-branch`. It
produces the private diff and JSON routing manifest, so commands should consume its
output rather than reimplementing path exclusions, line counts, or test-share math.

Each command file states its own token-frugality rules inline, in the phase they apply
to. This README is a map, not a rule store: no command loads it, so nothing here is
enforceable — treat every line above as a possibly-stale summary of the command file it
describes, never as the rule itself.

## Agents (`.claude/agents/*.md`)

| Agent | Role | Tools |
|---|---|---|
| `branch-reviewer` | Focused reviewer for one merged group of dimensions. It reads the prepared diff once and uses targeted evidence gathering. `.claude/agents/branch-reviewer.md` is the dimension source of truth. | Read, Grep, Glob, Bash (model: sonnet) |

Dimensions as of this writing: `bugs`, `logic`, `consistency`, `perf`,
`practices`, `security`, `alternatives`, plus `enforcement` (conditional —
lint/CI/hook diffs only) — this list is a convenience snapshot
and can drift; check the agent file for the current list and exact scope of
each (e.g. `security` covers hardcoded creds, CSRF/SSRF, PII/PDPA-APPI,
session-cookie flags; `consistency` covers cross-file contract drift like
i18n key parity or `agg_*` column renames).

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
  `/vps-loop-run`, which runs unattended: it may push feature branches and open
  draft PRs, but never pushes to `main` and never merges.
- `/address-my-pr-comments` never calls the GraphQL `resolveReviewThread`
  mutation — resolving is the reviewer's call.

## VPS operations

- Cron invokes a short `claude -p "/vps-loop-run"` wrapper; the command file owns
  orchestration. `NEXT_TASK.md` is local/untracked and missing or empty means no-op.
- Non-interactive SSH and cron shells do not source `~/.bashrc`. Put required OAuth
  variables in `/etc/environment` and expose binaries through `/usr/local/bin`.
- To trigger early, SSH to the VPS and run `/root/claude-loop.sh`; otherwise wait for
  cron. The loop operates on one item and never merges its own draft PR.
- The pre-push backend timeout is 240 seconds, while the small VPS can need roughly
  6.5 minutes. A timeout with no test failure is an infrastructure limitation, not
  evidence that tests failed; resolve it before weakening the gate.
