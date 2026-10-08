# CLAUDE.md

@AGENTS.md

## Claude Code specifics

The repository's rules live in `AGENTS.md`, imported above, so every agent reads the
same source. This section only maps them onto Claude Code tooling.

- Skills: load `transit-app-gotchas` before running tests, writing SQL, adding UI
  strings, or diffing branches. Load `postgres-perf` when optimizing queries or
  aggregates, and `maplibre-map` when editing the Map tab, basemaps, or map layers.
- The review pass is `/review-branch`.
  - Normal changes get one pass with two merged reviewers. Extra review is only for
    enforcement, high-risk paths, or material fixes.
  - Human prose outside the process docs may use its direct trivial path.
- `/cleanup-merged` is the post-merge cleanup.
- `.claude/settings.json` wires two hooks that enforce rules for Claude Code
  sessions:
  - `.claude/hooks/guard-dev-db.sh`, a wrapper around `guard_dev_db.py`, enforces
    the dev-database rule.
  - `.claude/hooks/guard-push-quality.sh` enforces the pre-push verification rule.
