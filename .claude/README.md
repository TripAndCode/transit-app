# .claude/commands and .claude/agents

Slash commands + subagents for PR review workflow. Related: `.claude/skills/`
(background knowledge, invoked automatically) vs these two dirs (workflows you
trigger explicitly).

## Commands (`.claude/commands/*.md`)

Invoke as `/name` from a Claude Code session.

| Command | Does | Reads/writes |
|---|---|---|
| `/review-branch` | Diffs branch vs `main`, dispatches `branch-reviewer` subagent once per dimension in parallel (fresh context each), synthesizes findings, then cleanup pass (`make check`). Runs 3x fresh-eyes per the review gate. | Read-only + `make check`. No commit/push. |
| `/pr-github` | Posts chosen `/review-branch` findings as inline `gh` comments on the PR. Also defines PR-description style (scannable, table-first, bold keywords). | Writes to GitHub via `gh`. |
| `/address-my-pr-comments` | Pulls unresolved review threads on your own PR (REST + GraphQL for resolve-state), judges each vs current code, drafts replies/fixes, **waits for per-thread approval** before posting or editing anything. Never resolves threads itself. | Reads via `gh`; writes only after explicit approval. |

Typical flow: `/review-branch` → `/pr-github` (post findings) → reviewer replies
→ `/address-my-pr-comments` (triage + fix + reply).

## Agents (`.claude/agents/*.md`)

| Agent | Role | Tools |
|---|---|---|
| `branch-reviewer` | Fresh-context principal-engineer reviewer for **one** dimension of a branch diff (dispatched by `/review-branch`, never called directly). `.claude/agents/branch-reviewer.md` is the source of truth for the dimension list — `/review-branch` reads it from there, not from this table. | Read, Grep, Glob, Bash (model: opus) |

Dimensions as of this writing: `bugs`, `logic`, `consistency`, `perf`,
`practices`, `security`, `alternatives` — this list is a convenience snapshot
and can drift; check the agent file for the current list and exact scope of
each (e.g. `security` covers hardcoded creds, CSRF/SSRF, PII/PDPA-APPI,
session-cookie flags; `consistency` covers cross-file contract drift like
i18n key parity or `agg_*` column renames).

## Guardrails baked into these files

- DB safety: any SQL is read-only against dev `transit`@5433; tests point at
  throwaway `transit_test`@5544. (See root `CLAUDE.md`.)
- No command here commits or pushes without explicit user go-ahead.
- `/address-my-pr-comments` never calls the GraphQL `resolveReviewThread`
  mutation — resolving is the reviewer's call.