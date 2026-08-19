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

## Findings so far

**Slice 1** (`pipeline/reports/`): the initial survey's "likely duplicated
aggregation/rounding/filter-building" guess was largely wrong on closer
reading — this family is already heavily hand-optimized (see `overview.py`'s
module docstring on `_fetch_grain` consolidating 12 dedup scans into 1) and
most apparent duplication is deliberate, documented divergence (e.g.
`filters.py`'s `_TIME_BAND_RANGES` comment: "Duplicated locally so this
module doesn't reach into a private name in another package"). The only
*actual* cross-file duplication found was `_round2`/`_MIN`, defined
identically in both `overview.py` and `rankings.py` (the former's docstring
even said "Mirrors pipeline.reports.rankings._round2") — consolidated into
`filters.py`, the family's existing shared module. `rankings.py` also turned
out to have solid indirect coverage via `tests/api/test_reports.py`
(both agg and live paths, rounding parity, tie semantics) despite lacking a
dedicated `test_rankings.py` — so no new characterization-test scaffolding
was needed beyond a small unit test for the relocated `_round2`. One
suspected-but-unfixed bug found along the way is in `NOTES.md`. Takeaway for
remaining slices: verify duplication claims by reading the code before
assuming a large simplification opportunity — this codebase already got a
real perf/complexity pass in PRs #75-79 and #184.

**Slice 2** (`pipeline/query/tools.py` + `tool_queries.py` + `meta_tools.py`):
mostly well-factored already — `tool_queries.py`'s repeated `if ch is None:
return []` guards and `_dedup_cte_ch(ctx)` calls are already using a shared
helper, not duplication. The one real, safe win: `route_stats`,
`segment_hotspots`, `time_pattern`, `schedule_realism`, and `trend_shift` in
`tools.py` each repeated an identical "route missing → route_arg_required" +
"route not registered → route_not_registered" guard (5 copies) — extracted
into `_require_registered_route`. Net -5 lines. One coverage gap found and
fixed: none of the five handlers' missing-route-arg branch had a direct
test — added `test_dispatch_missing_route_arg_returns_empty` (parametrized)
+ an en-locale variant before refactoring.

Also observed (not touched — flagged in `NOTES.md` as an architecture
question, not a bug): `meta_tools.py` has its own `_summary(text_jp,
text_en, locale)` helper, structurally different from `tools.py`'s
`_summary(template, lang, **vars)` central-table-lookup design. Two
different localization patterns coexist in the same tool-calling family.
Consolidating them is a real design decision (which pattern becomes
canonical, and it touches every `meta_tools.py` call site), not a
mechanical dedupe — left for a human call.

**Slice 3** (`api/routers/map.py`): unlike slices 1-2, this one did have a
real, confirmed win — `route_trips` and `route_stop_profile` each repeated an
identical ~20-line "does route_code exist in agg_route_daily, and if so
what's its most recent ClickHouse observation within 30 days of the
agency's latest activity" block verbatim (same SQL, same bound, same
None-checks), and `route_shape` repeated just the existence-check half of
the same pattern (with the same agg_route_daily-vs-agg_route_stats
rationale comment, in one case already saying "see route_trips above for
why" — the authors had already partially deduped the *documentation* but
not the code). Extracted into `_route_exists` + `_latest_route_observation`.
Net -32 lines in `api/routers/map.py`. One coverage gap found and fixed:
`route_stop_profile`'s "nonexistent route" and "stale route beyond 30-day
bound" branches had no direct test, despite `route_trips` already covering
the identical logic — added two characterization tests mirroring the
existing `route_trips` ones before refactoring. No `NOTES.md` addition this
time — nothing bug-shaped was found on a close read; this file's extensive
inline comments (measured costs, real-data trade-offs) made confirming
"this really is the same logic, safe to dedupe" straightforward. Takeaway:
duplication *is* sometimes real here (unlike slices 1-2) — worth reading
closely rather than assuming either way.

**Slice 4** (`pipeline/query/chat.py` + `router.py` + `llm_client.py`): a
lopsided result — `router.py` and `llm_client.py` were both already tight and
heavily documented (the embed-margin ambiguity guard, the provider fallback
ladder with its retry/timeout/rate-limit distinctions) and had nothing worth
touching. `chat.py` had the slice's real find: `chat_with_tools` repeated the
same ~35-line try/except-around-`dispatch()` scaffold **five times**
(build-mode, cache pre-hit, cache stage-2 JSON-fallback, cache stage-2 main
dispatch, flag-off path) — same four exception classes, near-identical
response-dict shaping, differing only in a few extra cache-bookkeeping keys
and a log-message suffix. Extracted `_dispatch_and_respond()`, parameterized
by `extra` (the differing dict keys) and `verb_suffix` (keeps per-site log
text exactly as before — confirmed no test asserts exact log strings, only
`exc_info` content). Left build-mode's block un-consolidated: its log
phrasing ("Build-mode dispatch for %s...") differs in shape, not just a
suffix, so folding it in would've added more parameterization than it saved.
Net **-85 lines** in `chat.py`. Two of the five sites (cache stage-2
JSON-fallback and main-dispatch) had no direct error-leakage characterization
test despite the other three being thoroughly covered in
`test_chat_error_leakage.py` — added both before refactoring. No `NOTES.md`
addition this time. Takeaway: even within one "slice," some files are
already clean (router.py, llm_client.py) while a sibling file in the same
slice has a real, large win (chat.py) — read each file independently rather
than extrapolating from one file's result to its neighbors.

## Slices

| # | Status | Slice (files) | Why | Coverage now | Risk |
|---|---|---|---|---|---|
| 1 | done | `pipeline/reports/overview.py` (1300L), `rankings.py` (733L), `forecast.py` (204L), `network.py` (113L), `filters.py` (128L) | Largest file in the repo; `rankings.py` has no dedicated test file; likely duplicated aggregation/rounding/filter-building across this "reports" family | Partial → good | Medium — feeds `agg_*`-backed report endpoints, not the live CH scan path |
| 2 | done | `pipeline/query/tools.py` (1166L), `tool_queries.py` (352L), `meta_tools.py` (667L) | Core of Ask-tab stage-3 tool-calling surface; likely overlapping SQL-building/formatting helpers | Good | Medium — `_LOCALES` strings (ja/en) are pinned exactly; diff harness must check them byte-for-byte |
| 3 | done | `api/routers/map.py` (1151L, ~2x the next-largest router) | Single file doing far more than its peers; route/shape/heatmap endpoints likely share extractable helpers | Good → good | Medium — touches the live ClickHouse scan path for `time_band`-filtered requests |
| 4 | done | `pipeline/query/chat.py` (817L), `router.py` (376L), `llm_client.py` (293L) | 3-stage Ask router (rules → e5-small NN → RAG+LLM); natural seams already exist per stage | Good → good | Higher — must preserve kill-switch/env-gate behavior exactly; no live LLM calls in tests |
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
