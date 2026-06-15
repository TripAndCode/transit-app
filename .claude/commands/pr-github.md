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
