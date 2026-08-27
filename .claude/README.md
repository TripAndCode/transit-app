# .claude/commands and .claude/agents

Slash commands + subagents for PR review workflow. Related: `.claude/skills/`
(background knowledge, invoked automatically) vs these two dirs (workflows you
trigger explicitly).

## Commands (`.claude/commands/*.md`)

Invoke as `/name` from a Claude Code session.

| Command | Does | Reads/writes |
|---|---|---|
| `/review-branch` | Diffs branch vs `main` once into a file, dispatches `branch-reviewer` subagents in parallel (fresh context each, handed the diff path) covering every dimension, with the call count scaled to the diff — synthesizes findings, then cleanup pass (`make check`). Runs 3x fresh-eyes per the review gate. Read the command file for the actual fan-out rule. | Read-only + `make check`. No commit/push. |
| `/pr-github` | Posts chosen `/review-branch` findings as inline `gh` comments on the PR. Also defines PR-description style (scannable, table-first, bold keywords). | Writes to GitHub via `gh`. |
| `/vps-loop-run` | Coordinator for one autonomous VPS-loop tick: state check → sync `main` → pick next actionable `NEXT_TASK.md` item → dispatch an isolated worker → verify via the `/review-branch` process → push + open a **draft** PR, then mark ready. Never merges. | Reads `NEXT_TASK.md`, appends its Status log; pushes feature branches + opens PRs via `gh`. |
| `/address-my-pr-comments` | Pulls unresolved review threads on your own PR (REST + GraphQL for resolve-state), judges each vs current code, drafts replies/fixes, **waits for per-thread approval** before posting or editing anything. Never resolves threads itself. | Reads via `gh`; writes only after explicit approval. |

Typical flow: `/review-branch` → `/pr-github` (post findings) → reviewer replies
→ `/address-my-pr-comments` (triage + fix + reply).

Each command file states its own token-frugality rules inline, in the phase they apply
to. This README is a map, not a rule store: no command loads it, so nothing here is
enforceable — treat every line above as a possibly-stale summary of the command file it
describes, never as the rule itself.

## Agents (`.claude/agents/*.md`)

| Agent | Role | Tools |
|---|---|---|
| `branch-reviewer` | Fresh-context principal-engineer reviewer for one or more named dimensions of a branch diff (dispatched by `/review-branch`, never called directly; small diffs get merged multi-dimension calls, large ones one call per dimension). `.claude/agents/branch-reviewer.md` is the source of truth for the dimension list — `/review-branch` reads it from there, not from this table. | Read, Grep, Glob, Bash (model: opus) |

Dimensions as of this writing: `bugs`, `logic`, `consistency`, `perf`,
`practices`, `security`, `alternatives`, plus `enforcement` (conditional —
lint/CI/hook diffs only) — this list is a convenience snapshot
and can drift; check the agent file for the current list and exact scope of
each (e.g. `security` covers hardcoded creds, CSRF/SSRF, PII/PDPA-APPI,
session-cookie flags; `consistency` covers cross-file contract drift like
i18n key parity or `agg_*` column renames).

## Guardrails baked into these files

- DB safety: any SQL is read-only against dev Postgres `transit`@5433 and the dev
  ClickHouse (`transit-ch`); tests point at throwaway `transit_test`@5544 and
  ClickHouse @8124. Postgres writes are additionally hook-blocked by
  `hooks/guard-dev-db.sh`. (See root `CLAUDE.md`.)
- No command here commits or pushes without explicit user go-ahead, except
  `/vps-loop-run`, which runs unattended: it may push feature branches and open
  draft PRs, but never pushes to `main` and never merges.
- `/address-my-pr-comments` never calls the GraphQL `resolveReviewThread`
  mutation — resolving is the reviewer's call.