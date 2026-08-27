# Refactor Plan

Behavior-preserving simplification pass, executed one slice at a time. Each
slice ships as its own branch/PR — the PR review is the checkpoint, not a
chat check-in. No behavior changes; anything that looks like a bug is noted
in `docs/refactor-notes.md` instead of being silently "fixed".

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
suspected-but-unfixed bug found along the way is in `docs/refactor-notes.md`. Takeaway for
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

Also observed (not touched — flagged in `docs/refactor-notes.md` as an architecture
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
existing `route_trips` ones before refactoring. No `docs/refactor-notes.md` addition this
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
`test_chat_error_leakage.py` — added both before refactoring. No `docs/refactor-notes.md`
addition this time. Takeaway: even within one "slice," some files are
already clean (router.py, llm_client.py) while a sibling file in the same
slice has a real, large win (chat.py) — read each file independently rather
than extrapolating from one file's result to its neighbors.

**Slice 5** (`pipeline/analyze.py`): the highest-risk slice yet — feeds every
`agg_*` table the default (unfiltered) read path serves from — so verification
leaned harder than prior slices: a new golden-fixture diff harness
(`scripts/dev/diff_analyze_slice5.py`) snapshots all 13 `agg_*` tables'
contents and byte/value-compares them against a saved baseline, on top of the
existing + 2 new characterization tests. The file itself is exceptionally
well-documented (dense inline comments justifying every non-obvious choice —
streaming over buffering, JST-not-UTC timestamp fixes, NULL-service
sentinels) and none of that was touched. The one real, safe win: 9 of the
aggregate builders (route_stats, route_hour, route_dow, route_hour_dow,
daily_trend, route_daily, route_daily_dist, hour_daily, stop_seq) repeated an
identical run-query → bulk-insert → log-row-count 3-statement sequence,
verbatim — extracted into `_build_and_insert()`. The three `has_static`-gated
builders (stop_daily, stop_routes, route_stop_daily) use a structurally
different INSERT...SELECT pattern and were correctly left alone. Two real
coverage gaps closed first: `agg_route_hour_dow` and `agg_route_daily` were
only ever exercised via direct test-fixture INSERTs in *other* test files
(test_forecast_heatmap.py, test_overview.py), never through analyze()'s own
SQL derivation — added direct characterization tests for both. No `docs/refactor-notes.md`
addition — nothing bug-shaped surfaced on a close read. Takeaway: the
highest-risk slices in this codebase are also often the most carefully
already-written ones (dense comments = prior authors already fought these
exact correctness battles) — the mechanical, boilerplate-only wins are still
there, but they're narrower than in a less-hardened codebase, and extra
verification rigor (a real golden-fixture harness, not just existing tests)
is worth the added effort when the blast radius is this large.

**Slice 6** (`pipeline/ingest.py`): another high-risk slice (raw `updates`
ingestion, recent perf work in #184) that turned out very clean on close
reading — every non-obvious choice (batch-flush sizing, the `seen`/`done`
divergence for crash-safety, DataError-vs-other-exception retry semantics,
SAVEPOINT scoping) is justified by dense, specific comments, several citing
exact wall-clock numbers from a real backfill incident. The tarball-member
loop and the loose-`.pb` loop are structurally parallel but deliberately not
merged — they differ in how they obtain raw bytes (tar member extraction
with a silent-skip for non-file members vs. a plain file read) and in commit
cadence (every 300 vs. every 500 iterations), so forcing them into one loop
would trade a real, working distinction for a marginal LOC savings, exactly
the over-engineering direction this refactor is supposed to avoid. The one
duplication found was narrower and later in each loop: both loops end with
an identical 6-line "buffer this file's parsed rows, flush if batch-full"
sequence, extracted into `_buffer_parsed()`. Net **+2 lines** (the extracted
helper's docstring costs slightly more than the two call sites save) — a
DRY/single-source-of-truth win, not a size reduction, and flagged here so
nobody's surprised the LOC delta is positive. No new characterization tests
were needed: 13 of `test_ingest.py`'s existing tests already directly
exercise both loops' buffering/dedup/batching/failure paths (including
DataError retry and non-DataError whole-batch-discard), so that suite itself
served as the verification harness — a from-scratch golden-fixture script
would have been redundant for a change this mechanical and already this
tightly pinned. No `docs/refactor-notes.md` addition — nothing bug-shaped found. Takeaway:
"nothing worth changing" and "modest DRY win with a slightly positive LOC
delta" are both fine, honest outcomes for a high-risk slice — the goal is
verified safety, not a LOC count going down.

**Slice 7** (frontend: `ThreadSidebar.tsx`, `RouteForecastSection.tsx`,
`api/hooks.ts`): first non-backend slice, so it followed CLAUDE.md's frontend
rules instead (no `useMemo`/`useCallback`/`React.memo` additions, i18n key
parity, no new hardcoded strings) rather than the DB/diff-harness pattern —
verification here is the five `frontend/` checks (`typecheck`, `test`,
`lint`, `lint:i18n`, `lint:i18n-strings`), not a golden-fixture harness.
`api/hooks.ts` turned out to have nothing worth touching: its repeated
`const authed = useIsAuthenticated()` lines across five conversation hooks
look like duplication but aren't safely extractable without either violating
rules-of-hooks or adding a pointless one-line wrapper hook — left alone.
`ThreadSidebar.tsx` had a real, confirmed duplication: its "Pinned" section
was a near-identical copy of each `groups.map()` item (same header + list
markup), differing only in an emoji prefix — folded into the `groups` array
as its first entry with an optional `emoji` field. Net -22 lines. Had no
dedicated test file (per the initial survey), so a characterization test was
written first, covering empty/loading states and confirming the Pinned
section renders before the date groups with its emoji-prefixed header — all
passed unchanged pre- and post-refactor. `RouteForecastSection.tsx` (already
covered by an existing `.test.tsx`) had a real, confirmed 3x-repeated
"filter to populated cells, then compute min/max (max floored at 1, min at 0
when empty)" pattern across `AgencyLanding`, `RouteDetail`'s per-hour cells,
and `RouteDetail`'s band-collapsed grid — extracted into `populatedRange()`.
No `docs/refactor-notes.md` addition — nothing bug-shaped found. Takeaway: the same
"verify duplication is real before touching it" discipline applies across
stacks, not just the backend — but the specific things worth checking
(rules-of-hooks safety, i18n key parity, avoiding new memoization) are
frontend-specific and worth a fresh read of CLAUDE.md's frontend section
before starting, not an assumption that backend lessons transfer directly.

## Slices

| # | Status | Slice (files) | Why | Coverage now | Risk |
|---|---|---|---|---|---|
| 1 | done | `pipeline/reports/overview.py` (1300L), `rankings.py` (733L), `forecast.py` (204L), `network.py` (113L), `filters.py` (128L) | Largest file in the repo; `rankings.py` has no dedicated test file; likely duplicated aggregation/rounding/filter-building across this "reports" family | Partial → good | Medium — feeds `agg_*`-backed report endpoints, not the live CH scan path |
| 2 | done | `pipeline/query/tools.py` (1166L), `tool_queries.py` (352L), `meta_tools.py` (667L) | Core of Ask-tab stage-3 tool-calling surface; likely overlapping SQL-building/formatting helpers | Good | Medium — `_LOCALES` strings (ja/en) are pinned exactly; diff harness must check them byte-for-byte |
| 3 | done | `api/routers/map.py` (1151L, ~2x the next-largest router) | Single file doing far more than its peers; route/shape/heatmap endpoints likely share extractable helpers | Good → good | Medium — touches the live ClickHouse scan path for `time_band`-filtered requests |
| 4 | done | `pipeline/query/chat.py` (817L), `router.py` (376L), `llm_client.py` (293L) | 3-stage Ask router (rules → e5-small NN → RAG+LLM); natural seams already exist per stage | Good → good | Higher — must preserve kill-switch/env-gate behavior exactly; no live LLM calls in tests |
| 5 | done | `pipeline/analyze.py` (704L) | Core `agg_*` aggregation logic; likely repeated per-agency loop patterns | Good → good | High — feeds every default (unfiltered) read path; output drift breaks every downstream report |
| 6 | done | `pipeline/ingest.py` (473L) | GTFS-RT ingest entry point | Good → good | High — touches raw `updates` ingestion; recent perf work here (#184), check for overlap |
| 7 | done | Frontend: `ThreadSidebar.tsx` (591L), `RouteForecastSection.tsx` (649L), `api/hooks.ts` (547L) | Largest frontend files; `hooks.ts` and `ThreadSidebar` have no dedicated test file | Partial/weak → good (ThreadSidebar) | Low — pure frontend, no DB; must preserve i18n key parity and React Compiler purity rules |
| 8 | done | `api/routers/auth.py` (513L), `conversations.py` (570L) | Next tier of large routers after `map.py` | Good | Medium — auth-adjacent, be conservative |

Order is priority (complexity × duplication × value, discounted by risk).
Starting at #1 — backend, deterministic outputs suit the golden-fixture diff
approach well, and it's isolated from the live-scan and ingest paths.

**Slice 8** (`api/routers/auth.py` + `conversations.py`, the last slice): the
most auth-conservative slice by design. `auth.py` was read in full and left
**byte-identical** — its one candidate duplication (session-row-insert +
login-event sequence, repeated between the OAuth callback and local_login)
sits inside the actual session-minting control flow, exactly the kind of
"looks safe but touches session/token handling" case this slice's brief said
to flag rather than judgment-call (see `docs/refactor-notes.md`). `conversations.py` had a
real, safe win outside the auth-sensitive core: `get_conversation`,
`update_conversation`, `delete_conversation`, `list_messages`, and
`append_message_endpoint`'s ownership check each repeated an identical
`try: ... except (_conv.PermissionDenied, LookupError): raise
HTTPException(404, "not found")` block — pure response-shaping around an
exception already raised by `pipeline.query.conversations`, not an
authorization decision itself. Consolidated into `_owned_or_404()`. Net **-7
lines**. Two of the five call sites (get_conversation's 404 path) already had
direct API-layer test coverage; the other four (update/delete/list_messages/
append) didn't — added one consolidated characterization test covering all
four before refactoring. `followup_endpoint` had the same two duplications
(ownership-404, and a repeated `too_long`/`llm_error` mapping) but was left
untouched: it has zero existing test coverage anywhere in `tests/` and is a
kill-switch-gated LLM-adjacent feature, so touching it here would mean
writing a new characterization-test suite from scratch as a refactor side
effect rather than a mechanical dedupe — flagged in `docs/refactor-notes.md` instead.
Takeaway for future work on this codebase: "auth-adjacent" doesn't mean
"nothing to simplify" — it means read closely enough to separate the actual
authorization/session logic (untouchable here) from the response-shaping
code that happens to sit next to it (fair game), and coverage gaps around a
kill-switched feature are a real reason to defer a refactor, not just an
excuse.

## Final summary (Phase 4 report)

All 8 planned slices are **done**, none blocked. Total across all slices:
**8 PRs merged** (#185–#191, plus this slice's PR), **0 diff-harness
divergences** on every slice that built one (slices 1, 5), **0 behavior
changes** confirmed by the full relevant test suite on every slice. Rough
net LOC delta by slice: #1 -9, #2 -5, #3 -32, #4 -85, #5 net reduction from
a 9-way builder consolidation (see slice 5's finding above — exact delta in
PR #189), #6 **+2** (a DRY win, not a size cut), #7 -22 (`ThreadSidebar.tsx`)
plus a separate real dedup in `RouteForecastSection.tsx`, #8 -7. Total
backend+frontend line reduction is modest (roughly 150-160 lines net across
the whole pass) — this codebase had already been through real
complexity/perf work (PRs #75-79, #184) before this pass started, so the
honest finding across almost every slice was "verify before assuming
duplication exists," not "big rewrite opportunity." Several slices (2's
`router.py`/`llm_client.py`, 6 as a whole net-positive, 8's `auth.py`)
correctly concluded "nothing worth changing" or "small/negative LOC delta"
rather than forcing a change to show progress — that restraint is treated as
a successful outcome of this process, not a failure to find work.

**`docs/refactor-notes.md` entries for human triage** (none were auto-fixed; all are
flag-only per this refactor's "no behavior changes" rule):
1. **Slice 1** — inconsistent tie-break on ranking sorts: `overview.py`'s
   `_movers`/`_concentration` (slow path) and `rankings.py`'s
   `_compare_ranking_live` explicitly break ties on `route_code` (citing a
   previously-fixed non-determinism bug); `compute_ranking`,
   `compute_on_time`, `compute_worst_5min`, `compute_dow_ranking`, and
   `_concentration`'s fast path don't. Worth a deliberate decision on
   whether to extend the fix.
2. **Slice 2** — two coexisting localization-string architectures in one
   merged tool-calling surface: `tools.py`'s central `_LOCALES` table vs.
   `meta_tools.py`'s inline `_summary(text_jp, text_en, locale)`. Real
   design decision (which becomes canonical), not a mechanical dedupe.
3. **Slice 8** — two more real-but-untouched duplications: the auth.py
   session+login-event sequence (left alone as in-scope-but-too-sensitive),
   and `followup_endpoint`'s ownership-404 + error-mapping duplication
   (left alone for lack of any existing test coverage on a kill-switched
   LLM-adjacent endpoint — writing that coverage is a reasonable follow-up
   task in its own right, separate from this mechanical refactor pass).

No slices were blocked or reverted. Test containers (`transit-test-pg`
`:5544`, `transit-test-ch` `:8124`) were reused across all 8 slices and are
no longer needed now that the plan is complete.

## Entry points with weak/no dedicated test coverage (from initial survey)
- `pipeline/reports/rankings.py` — only indirect coverage via `test_reports.py`/`test_tool_queries.py`
- `frontend/src/api/hooks.ts` — only indirect coverage via component tests
- `frontend/src/components/ThreadSidebar.tsx` — no `.test.tsx` found
- `db/clickhouse/bootstrap.py` — no test file (low risk, 38L)
- `api/routers/network.py`, `debug.py`, `ask_dashboard.py`, `internal.py` — each matched only one test file; confirm real coverage before treating as "done" when their slice comes up

## Ambiguous cases (→ docs/refactor-notes.md when found)
None identified yet from the structural survey pass — real ambiguous-case
flagging happens per-slice during close reading, not decided upfront.
