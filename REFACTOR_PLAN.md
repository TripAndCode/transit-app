# Refactor Plan

Behavior-preserving simplification pass, executed one slice at a time. Each
slice ships as its own branch/PR — the PR review is the checkpoint, not a
chat check-in. No behavior changes; anything that looks like a bug is noted
in `NOTES.md` instead of being silently "fixed".

Process per slice:
1. **Baseline** — identify the slice's public entry points, write
   characterization tests for any that lack coverage (pinning CURRENT
   behavior, quirks included), save golden outputs, write/extend a diff
   harness that can re-run those fixtures against the refactored code.
2. **Refactor** — simplify only (dedup, remove dead code, reduce
   over-engineering). No behavior changes.
3. **Verify** — diff harness shows 0 divergences; full relevant test suite
   (`make check` scope) passes.
4. **Ship** — commit(s) on a dedicated branch, push, open PR. Mark slice
   "done" here. If verification fails, revert the slice's changes and mark
   "blocked" with the reason — move to the next slice rather than getting
   stuck.

Test infra prerequisite (per `CLAUDE.md`): throwaway Postgres on `:5544`
(built from `db/`, not the bare `pgvector/pgvector` image — needs
PostGIS+pgvector+pg_trgm) + throwaway ClickHouse on `:8124` via
`make ch-test`, with `RUN_CH_INTEGRATION=1` and the `CLICKHOUSE_*` env vars
set — otherwise ClickHouse-gated tests silently skip instead of failing.
Never touch the dev Postgres (`:5433`) or dev ClickHouse (`transit-ch`) —
both are read-only, real-data instances per `CLAUDE.md`.

## Slices

| # | Status | Slice (files) | Why | Coverage now | Risk |
|---|---|---|---|---|---|
| 1 | pending | `pipeline/reports/overview.py` (1300L), `rankings.py` (733L), `forecast.py` (204L), `network.py` (113L), `filters.py` (128L) | Largest file in the repo; `rankings.py` has no dedicated test file; likely duplicated aggregation/rounding/filter-building across this "reports" family | Partial | Medium — feeds `agg_*`-backed report endpoints, not the live CH scan path |
| 2 | pending | `pipeline/query/tools.py` (1166L), `tool_queries.py` (352L), `meta_tools.py` (667L) | Core of Ask-tab stage-3 tool-calling surface; likely overlapping SQL-building/formatting helpers | Good | Medium — `_LOCALES` strings (ja/en) are pinned exactly; diff harness must check them byte-for-byte |
| 3 | pending | `api/routers/map.py` (1151L, ~2x the next-largest router) | Single file doing far more than its peers; route/shape/heatmap endpoints likely share extractable helpers | Good | Medium — touches the live ClickHouse scan path for `time_band`-filtered requests |
| 4 | pending | `pipeline/query/chat.py` (817L), `router.py` (376L), `llm_client.py` (293L) | 3-stage Ask router (rules → e5-small NN → RAG+LLM); natural seams already exist per stage | Good | Higher — must preserve kill-switch/env-gate behavior exactly; no live LLM calls in tests |
| 5 | pending | `pipeline/analyze.py` (704L) | Core `agg_*` aggregation logic; likely repeated per-agency loop patterns | Good | High — feeds every default (unfiltered) read path; output drift breaks every downstream report |
| 6 | pending | `pipeline/ingest.py` (473L) | GTFS-RT ingest entry point | Good | High — touches raw `updates` ingestion; recent perf work here (#184), check for overlap |
| 7 | pending | Frontend: `ThreadSidebar.tsx` (591L), `RouteForecastSection.tsx` (649L), `api/hooks.ts` (547L) | Largest frontend files; `hooks.ts` and `ThreadSidebar` have no dedicated test file | Partial/weak | Low — pure frontend, no DB; must preserve i18n key parity and React Compiler purity rules |
| 8 | pending | `api/routers/auth.py` (513L), `conversations.py` (570L) | Next tier of large routers after `map.py` | Good | Medium — auth-adjacent, be conservative |

Order is priority (complexity × duplication × value, discounted by risk).
Starting at #1 — backend, deterministic outputs suit the golden-fixture diff
approach well, and it's isolated from the live-scan and ingest paths.

## Entry points with weak/no dedicated test coverage (from initial survey)
- `pipeline/reports/rankings.py` — only indirect coverage via `test_reports.py`/`test_tool_queries.py`
- `frontend/src/api/hooks.ts` — only indirect coverage via component tests
- `frontend/src/components/ThreadSidebar.tsx` — no `.test.tsx` found
- `db/clickhouse/bootstrap.py` — no test file (low risk, 38L)
- `api/routers/network.py`, `debug.py`, `ask_dashboard.py`, `internal.py` — each matched only one test file; confirm real coverage before treating as "done" when their slice comes up

## Ambiguous cases (→ NOTES.md when found)
None identified yet from the structural survey pass — real ambiguous-case
flagging happens per-slice during close reading, not decided upfront.
