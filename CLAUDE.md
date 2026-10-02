# CLAUDE.md

@AGENTS.md

## Claude Code specifics

The repository's rules live in `AGENTS.md`, imported above, so every agent reads the
same source. This section only maps them onto Claude Code tooling.

- Skills: load `transit-app-gotchas` before running tests, writing SQL, adding UI
  strings, or diffing branches. Load `postgres-perf` when optimizing queries or
  aggregates, and `maplibre-map` when editing the Map tab, basemaps, or map layers.
- The PR review pass is `/review-branch`.
  - Run it once per PR, before opening as a draft.
  - Normal changes get one pass with two merged reviewers. Extra review is only for
    enforcement, high-risk paths, or material fixes.
  - Human prose outside `.claude/**`, `CLAUDE.md` and `AGENTS.md` may use its direct
    trivial path. Those three are executable process docs.
- After a PR merge, `/cleanup-merged` runs `AGENTS.md`'s post-merge cleanup in
  persistent local/VPS clones.
- Hooks in `.claude/settings.json` enforce two `AGENTS.md` rules for Claude Code
  sessions:
  - `.claude/hooks/guard_dev_db.py` refuses writes to the dev databases.
    `DEV_PORTS` must name every port a dev instance is published on.
  - `.claude/hooks/guard-push-quality.sh` runs the verification checks on the pushed
    worktree before any `git push`. Its header states what it leaves uncovered.
