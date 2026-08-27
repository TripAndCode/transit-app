---
name: pr-github
description: Post selected branch-review findings as inline comments on the GitHub PR for the current branch, via the gh CLI.
---

Post selected review findings as inline comments on the GitHub PR for the current branch.

## Hold for approval (hard constraint, inline-findings flow only)
Nothing gets posted as a review comment without an explicit pick from the user in
step 2. Listing the findings is not approval — post only the findings the user
actually selected, never "post everything" by default.
This gates Step 3 (posting the comments). It does NOT gate *editing the body text*
of a PR that `/vps-loop-run` Step 6 already created as a draft — that runs unattended
with no user present to pick. It is never licence to create a PR outside that flow: in
an interactive session Step 1's "stop and offer" still applies, and every PR this repo
opens starts as `--draft` per CLAUDE.md.

## Auth
- Use the `gh` CLI — already authenticated via keyring. Do NOT ask for, paste, or
  store any API token. If a call fails, check `gh auth status`.

## Steps
1. Resolve the PR: `gh pr view --json number,url,headRefName,headRefOid`.
   If none exists, stop and offer `gh pr create --draft` (per CLAUDE.md, PRs open as
   drafts until `/review-branch` is confirmed clean).
2. List the findings from the latest branch review and get the user's pick (see "Hold
   for approval" — that's the single statement of the rule).
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
- **State the origin**, right after the one-line summary: `**Origin:**
  Interactive session` if the work came from a human-driven session, or
  `**Origin:** Autonomous VPS loop (item N)` if `/vps-loop-run` produced it
  (per CLAUDE.md's "Autonomous VPS loop"). Lets a reviewer tell at a glance
  which review posture applies (VPS-loop PRs never get merged without an
  explicit human go-ahead, regardless of how clean the review came back).
- Keep sections short and titled: What · Affected · Behaviour · Fixes · Tests ·
  Verification (include only those that apply). End with the Claude Code trailer.

Update via `gh pr edit <number> --body "<markdown>"` (and `--title` if it no longer fits).
