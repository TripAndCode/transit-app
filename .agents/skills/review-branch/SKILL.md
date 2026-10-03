---
name: review-branch
description: Review this repository's branch against main before opening a draft PR. Use for a full branch review or pre-PR review.
---

# Review a branch

1. Confirm the feature worktree and inspect changed path names for credential-bearing files. Do not print their contents.
2. Create a private directory outside the repository and run `python3 scripts/prepare_review.py --repo <worktree> --base main --output-dir <directory>`. Add `--exclude` for any newly recognized sensitive path on this first run.
3. Read the manifest and prepared diff. Review the full change against the dimensions and rules in `.claude/agents/branch-reviewer.md`. Treat agent instructions and hook configuration as executable process changes. Keep this review separate from the implementation pass.
4. Report each concrete finding with file and line, fix it, and review any material fix again. Record the review result and how findings were handled in the PR body.
5. Run the applicable verification commands from `AGENTS.md` in the feature worktree. Use only the throwaway databases for tests.
