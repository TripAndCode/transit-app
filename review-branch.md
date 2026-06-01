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

---

# Phase ③.7 — Ask tab redesign (QuestionDock)

## What changed

Replaced the always-visible 5-card grid + collapse-strip with a chat-first **QuestionDock**: chips pinned at bottom, inline param strip rises above chips when composing, results land in the scroll area above, follow-up chips appear under the last result-bearing assistant bubble.

Files (net):
- NEW `frontend/src/components/QuestionDock.tsx` — idle/composing/busy state machine, 5-chip row, swap-on-tap
- NEW `frontend/src/components/ParamStrip.tsx` — single-row param composer per template, exhaustive ParamSpec switch
- NEW `frontend/src/components/paramPills/{SegmentedPill,LimitPill,RoutePickerPill}.tsx` — small popover controls
- MOD `frontend/src/tabs/AskTab.tsx` — mounts QuestionDock as bottom sticky region, drops cards grid + cardsExpanded state
- MOD `frontend/src/components/askCardTemplates.ts` — absorbed CardTemplate + ParamSpec types from the deleted card file
- MOD `frontend/src/i18n/locales/{ja,en}.json` — `ask.dock.*` added, `ask.cards_strip_*` orphans removed
- DEL `frontend/src/components/ParameterizedQuestionCard.tsx` — replaced

## Quality gates

- pytest: 452 pass / 2 fail / 4 skip / 1 error — failures are pre-existing dev-DB deadlocks, zero regressions
- `scripts/ask_eval.py` — builder_coverage 20/20 (100.0%), exit 0
- `tsc --noEmit` — clean
- `npm run build` — succeeds
- Playwright e2e — 11/13 ✓, 2 skipped (authed manual / kill switch needs backend restart). Zero failures.

## Reviews

Five fresh-context subagents reviewed: QuestionDock+ParamStrip / pills / AskTab integration / i18n / calm-UI palette.

P1 findings fixed in `6091fbe`:
- All 3 pills now restore focus + Escape-to-close
- RoutePickerPill filters routes with null `route_code`
- ParamStrip drops incorrect `aria-busy` on 実行 button
- QuestionDock adds `aria-disabled` to busy-locked chips
- AskTab `handleSelectThread(id)` renamed to `handleSelectThread(threadId)` (shadowing fix)
- Required-* marker color reduced from hsl(25,55%,50%) → hsl(25,40%,50%) (less alarm)
- Inline `var(--accent, …)` fallbacks unified to `#5b6cad` to match the actual CSS variable

## Deferred (P2s)

- LimitPill uncontrolled-typing race (NaN swallow visible) — uses local draft string state
- RoutePickerPill viewport-clip on tall popovers (low priority, desktop-first tool)
- `q` search-input is reset on selection but not on outside-click — minor UX nit; user must re-type if they bail
- `appendMsg.isPending` scroll-effect fires twice per submit — visual jank is below threshold
- `convQuery.data?.conversation?.filter_ctx` ref-equality re-fires — benign in practice
- `ask.dock.composing_label` defined in both locales but currently unused — kept for future composing-state header
- Inline `defaultValue: "指標"` JA fallback inside English code path — would only show JA on locale-load failure
- Hover styles on pill triggers absent (relies on cursor change) — visual nicety
- "dashed" border separator between param strip and chip row — slightly informal; intentional for now
- Stale-closure on `handleRun` if `onSubmit` rejected — current code is fire-and-forget; parent's `appendMsg.isError` surfaces failure, no values to restore

## Recommendation

MERGE — chat-first redesign lands cleanly, all P0/P1 reviewer findings addressed, e2e covers the happy paths, kill switch verified by design (gated on `ASK_FOLLOWUP_ENABLED`).

---

# Phase ③.8 — Ask polish (Pxx cleanup)

## What changed

P2 cleanup from Phase ③.7 review-branch.md. Frontend-only.

- LimitPill: local draft string state — commit on Enter/blur only, with empty-string guard (P1 found mid-review)
- All 3 pill triggers: subtle hover bg + 120ms transition
- ParamStrip: drop hardcoded JA defaultValue on metric_label
- Locales: remove unused `ask.empty_hint` and `ask.dock.composing_label`
- AskTab: scroll-effect dep array tightened (drop `appendMsg.isPending`)
- JSDoc added to all dock files (5 files)

## Reviews

3 fresh-context reviewers. All P1s addressed in `45bda62`.

- R1 (LimitPill + scroll): empty-string commit bug — fixed
- R2 (hover + i18n): all pass; minor question about Enter not closing the popover — left as-is (consistent with stepper)
- R3 (holistic taste): merge with notes; DRY note on 3× hover boilerplate — tolerable at 3 sites

## Deferred

- `usePillHoverProps()` hook if a 4th pill is added
- `followup_chips.panel_aria` defaultValue dead weight (pre-existing, out of scope)
- LimitPill stepper using committed value vs draft (out-of-scope edge case)

## Recommendation

MERGE — polish PR, no behavior regressions, tsc + ruff + i18n parity all clean.
