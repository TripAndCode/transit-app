# Phase ③.5 Holistic Review Summary

**Branch**: `worktree-ask-thread-guided`
**Review date**: 2026-05-31
**Reviewer**: Claude Sonnet 4.6 (post-bugfix holistic pass)

---

## What Phase ③.5 Changed

Phase ③.5 replaced the chip-catalog interaction model (a static list of pre-canned chips) with two new surfaces:

1. **Dashboard cards** — three read-only analysis panels always visible on the empty-thread state:
   - `DelayHeatmap`: route × DOW or hour-band avg-delay grid (top 15 routes)
   - `AnomalyTimeline`: 30-day daily avg with ±σ outlier markers
   - `MoversList`: top-N routes by |Δ avg-delay| current vs prior window

2. **Parameterized question cards** — five interactive `ParameterizedQuestionCard` components that let the user dial in parameters (k, service_type, route, granularity) and submit a structured tool call:
   - `top_delay` → `top_n(metric=avg_delay, k=…, service_type=…)`
   - `ontime_rank` → `on_time(k=…, best_first=…)`
   - `route_trend` → `trend(route_code=…, granularity=…)`
   - `weekday_vs_weekend` → `cmp_service(route_code=…)`
   - `route_overview` → `route_stats(route_code=…)`

Supporting changes:
- `pipeline/dashboard_queries.py` — SQL layer for the three panels
- `api/routers/ask_dashboard.py` — three GET endpoints
- `tests/ask_eval/gold_questions.jsonl` — 20 parameterized-card gold entries
- `ChipCatalog` removed; `chip_catalog` table and related API/types gone
- i18n keys added for dashboard/card strings

---

## What the 5 Reviews Found (compressed by category)

**Dispatch / routing**
- BUG-1 (ship-blocker): `on_time`, `trend`, `cmp_service` had no `_HANDLERS` entries; dispatch fell through to "unsupported_tool" — 3 of 5 cards dead on arrival
- `route_code` param name used by cards but handlers all read `route` — no remapping existed

**Arg normalization**
- BUG-2 (ship-blocker): `top_n` reads `n`; cards send `k`; user slider had zero effect. Same problem in `on_time_rate`. Gold eval uses `k` as canonical

**Frontend data flow**
- BUG-3 (ship-blocker): `handleCardSubmit` destructures `user_summary` but `appendMsg.mutate` call omitted it; `AppendMessageVars` had no `user_summary` field; user bubble showed machine `key=value` strings

**Filter context**
- BUG-4 (ship-blocker): `_DEDUPED_CTE` only filtered on `agency_id + captured_at::date`; `ctx.dow`, `ctx.time_band`, `ctx.service`, `ctx.routes` silently ignored for heatmap and anomalies; `movers` also ignored `ctx.from_date`. Endpoints accepted no `routes` query param

**Accessibility / i18n**
- BUG-5 (P1): Hardcoded JA `データなし` in null-cell aria-label; `aria-pressed` and `aria-selected` both on same `role="tab"` button — conflicting ARIA attributes

**Visual / rendering**
- BUG-6 (P1): `AnomalyTimeline` renders three y-axis labels even when `std===0`; all three collapse to the same pixel and overlap

---

## Fixes Applied in This Pass

| Bug | Commit | Fix summary |
|-----|--------|-------------|
| BUG-1 + route_code | `8ac3e7c` | Add `_TOOL_ALIASES` map in `dispatch()` (on_time→on_time_rate, trend→time_series, cmp_service→compare_segments); normalise `route_code`→`route` before handler dispatch |
| BUG-2 | `8ac3e7c` | `_tool_top_n` and `_tool_on_time_rate` read `k` first, fall back to `n`; eval gold uses `k` as canonical, remains 20/20 |
| BUG-3 | `e25eeda` | Extend `AppendMessageVars` with optional `user_summary`; `useAppendMessage` uses caller-supplied label over `builderSummary()` fallback; `AskTab.handleCardSubmit` passes `user_summary` |
| BUG-4 | `6ea0302` | Replace `_DEDUPED_CTE` string with `_deduped_cte(agency_id, ctx)` helper using `build_updates_filter()`; `movers()` uses `ctx.from_date` as window start + applies filter to both CTEs; add `routes: list[str]` to all 3 endpoints |
| BUG-5 | `8c0b710` | Add `ask.dashboard.heatmap.no_data` to ja/en locale files; replace hardcoded `データなし` with `t(…no_data)`; remove `aria-pressed` from `role="tab"` button |
| BUG-6 | `8c0b710` | Suppress ±σ y-axis labels when `std === 0`; render mean label only |

---

## What Is NOT Fixed (Deferred)

None of the six bugs were deferred. All four ship-blockers and both P1 issues are resolved.

**Known pre-existing issues not introduced by Phase ③.5** (tracked separately):
- Chunk size warning from Vite build (1313 kB bundle) — pre-existing, not Phase ③.5 scope
- 16 asyncpg deadlock ERRORs in the test suite on the shared dev DB at 5433 — race condition in parallel test isolation, pre-dates this branch

---

## Final Test / Eval / Build Status

| Check | Result |
|-------|--------|
| `poetry run pytest` | 436 passed, 4 skipped, 16 errors (errors = pre-existing asyncpg deadlocks on shared dev DB, not regressions) |
| `poetry run python scripts/ask_eval.py` | builder_coverage 20/20 (100%) |
| `npx tsc --noEmit` | 0 errors |
| `npm run build` | built in 3.88s, 0 errors (chunk size warning is pre-existing) |

---

## Recommendation

**MERGE** — all ship-blockers and P1 issues are fixed, eval 20/20, TypeScript clean, build clean, 436 tests pass. The 16 test errors are pre-existing asyncpg deadlock races on the shared dev DB and are not regressions from this branch.
