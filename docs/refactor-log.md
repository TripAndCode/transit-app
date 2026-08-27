# Refactor log

Append-only record of completed steps from the autonomous VPS loop's backlog
(see `NEXT_TASK.md`, and the design rationale in
`docs/superpowers/specs/2026-08-27-agent-friendly-dev-refactor-design.md`,
which is itself untracked/local per this repo's `docs/*` convention). Each
entry is added as part of the same PR that completes the step (the worker's
own commit, occasionally followed by a small coordinator commit filling in
the PR number once it's known).

Format: `- YYYY-MM-DD: <one-line summary of what was done> (PR #NNN)`

## Entries

- 2026-08-27: Added `docs/features/ask-tab.md` and `docs/features/map-tab.md` (feature-map docs for the Ask and Map tabs: UI entry point, request path, key files, manual verification steps, i18n notes), plus a `!docs/features/` negation in `.gitignore` so they aren't silently skipped by `docs/*`. Doc-only, no code changes. (PR #pending)
- 2026-08-27: Added a `## Process` section to `CLAUDE.md`: a rule to capture a repeated mistake in `transit-app-gotchas` (or the relevant skill) immediately, same session as the second occurrence, plus a short enforcement-ladder paragraph (codebase → static analysis/CI → bot rules → skills → style guide) noting a rule flagged 2+ times in review should be promoted up a layer. Doc-only, no code changes. (PR #pending)
