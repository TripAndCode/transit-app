# CLAUDE.md

@AGENTS.md

## Claude Code specifics

The repository's rules live in `AGENTS.md`, imported above, so every agent reads the
same source. This section only maps them onto Claude Code tooling.

- Skills: load `transit-app-gotchas` before running tests, writing SQL, adding UI
  strings, or diffing branches. Load `gtfs-rt-semantics` before changing ingest,
  analyze, an aggregate, a report metric, or date and service-day handling. Load
  `postgres-perf` when optimizing queries or aggregates, and `maplibre-map` when
  editing the Map tab, basemaps, or map layers.
- Read `docs/features/audience.md` before proposing, building or reviewing anything a
  user sees (`AGENTS.md`, Product direction).
- The review pass is `/review-branch`.
  - Normal changes get one pass with two merged reviewers. Extra review is only for
    enforcement, high-risk paths, or material fixes.
  - Human prose outside the process docs may use its direct trivial path.
- `/cleanup-merged` is the post-merge cleanup.
- `.claude/settings.json` wires three hooks that enforce rules for Claude Code
  sessions:
  - `.claude/hooks/guard-dev-db.sh`, a wrapper around `guard_dev_db.py`, enforces
    the dev-database rule.
  - `.claude/hooks/guard-git-shared.sh`, a wrapper around `guard_git_shared.py`,
    enforces the shared-clone git rule: no push that lands on `main`, no force-push
    spelled `--force`, `-f` or `+refspec`, no `stash pop`/`drop`/`clear`.
  - `.claude/hooks/guard-push-quality.sh` enforces the pre-push verification rule.
