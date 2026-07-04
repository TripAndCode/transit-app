---
name: pr-github
description: Post selected branch-review findings as inline comments on the GitHub PR for the current branch, via the gh CLI.
---

Post selected review findings as inline comments on the GitHub PR for the current branch.

## Auth
- Use the `gh` CLI — already authenticated via keyring. Do NOT ask for, paste, or
  store any API token. If a call fails, check `gh auth status`.

## Steps
1. Resolve the PR: `gh pr view --json number,url,headRefName,headRefOid`.
   If none exists, stop and offer `gh pr create`.
2. List the findings from the latest branch review; let the user pick which to post.
3. For each selected finding, post an inline review comment anchored to the exact
   file + line on the PR head commit:
   `gh api repos/{owner}/{repo}/pulls/{number}/comments -f body=... -f commit_id=<headRefOid> -f path=... -F line=... -f side=RIGHT`
4. Report posted comments with their URLs. Never echo any token.

## PR description style (DEFAULT when creating OR updating a PR body)

Write PR descriptions to be **scanned, not read**. Default to structure over prose:

- **Lead with affected scope.** First section after the one-line summary is a
  **table of affected routers / tables / tabs** (what changed, where). State
  explicitly what is NOT affected when it's easy to assume otherwise (e.g. "other
  agencies' aggregates untouched").
- **Bullets and tables over paragraphs.** No multi-sentence explanation blocks.
  One idea per bullet. Use tables for "issue → fix", "endpoint → behaviour".
- **A small diagram** for any flow/state/data-path that's clearer shown than told —
  GitHub renders Mermaid natively, so a fenced ```mermaid``` block or plain ASCII
  both work; keep it to a handful of lines.
- **Bold the keywords** in each bullet so the eye lands on them.
- **Cut prose to the load-bearing clause.** Move rationale into the table cell /
  bullet it belongs to; don't write a paragraph to set it up.
- Keep sections short and titled: What · Affected · Behaviour · Fixes · Tests ·
  Verification (include only those that apply). End with the Claude Code trailer.

Update via `gh pr edit <number> --body "<markdown>"` (and `--title` if it no longer fits).
