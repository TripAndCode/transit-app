# Refactor Notes

Things noticed during behavior-preserving refactor slices that look like bugs
or ambiguous behavior, deliberately NOT fixed as part of the simplification
work. Each entry names the slice it came from.

## Slice 1 — `pipeline/reports/` family

### Ambiguous — needs human decision: inconsistent tie-break on ranking sorts

**Resolved** in `fix/rankings-tie-break-consistency` (deterministic tie-break
extended to every remaining ranking function, plus `_top_delayed_routes`'s
fast path, found during the fix). Original text kept below for history.

`pipeline/reports/overview.py`'s `_movers` (~line 1008, `deltas.sort(key=lambda
x: (x[3], x[0]))`) and `_concentration`'s slow path (~line 647,
`sorted(by_route.items(), key=lambda kv: (-kv[1][1], kv[0]))`) both explicitly
add `route_code` as a secondary sort key. The comment at `overview.py:641-643`
explains why: "Ties are broken by route_code so the top-20 cut is
reproducible — the ClickHouse `ORDER BY total_late_min DESC` this replaces
left tied rows in an arbitrary, run-to-run-unstable order." Likewise
`rankings.py`'s `_compare_ranking_live` (~line 554) sorts on
`(-abs(delta), route_code)`, citing "same bug class already fixed for movers
ranking in overview.py."

However, the following sorts/ORDER BYs in the same family have **no**
tie-break on ties, despite reading from the same non-deterministic-order
sources (ClickHouse GROUP BY, or Postgres GROUP BY without an ORDER BY on the
grouping key):

- `rankings.py::compute_ranking` — `out.sort(key=lambda t: t[2], reverse=...)`
- `rankings.py::_ranking_live` — `ORDER BY avg_min {order}` (SQL, no tie-break)
- `rankings.py::compute_on_time` / `_on_time_live` — same pattern
- `rankings.py::compute_worst_5min` / `_worst_5min_live` — same pattern
- `rankings.py::compute_dow_ranking` (`ORDER BY avg_min DESC NULLS LAST`) /
  `_dow_ranking_live` (`ORDER BY avg_min DESC`) — same pattern
- `overview.py::_concentration`'s **fast path** (agg SQL,
  `ORDER BY total_late_min DESC NULLS LAST`) — the fast path was not given the
  same route_code tie-break its own slow path received a few lines below it

This could be intentional (e.g. the fast-path-only cases may see few enough
exact ties in practice, or a stable Postgres scan order may make it a
non-issue there in practice even though it's not guaranteed by the SQL
standard) — but given the codebase has evidently hit and fixed this exact bug
class at least twice (movers, compare_ranking), it's worth a deliberate
decision on whether the remaining `ranking`/`on_time`/`worst_5min`/
`dow_ranking` report types (and `_concentration`'s fast path) should get the
same treatment, rather than leaving the inconsistency unresolved by default.
Not fixed here — flagged only, per the refactor's "no behavior changes"
constraint.

### Ambiguous — needs human decision: same unguarded-tie shape, three more sites

Found while doing a comment-documentation pass (comments-only, no logic
touched) on the `pipeline/reports/` family. `fix/rankings-tie-break-consistency`
above fixed every ranking sort inside `rankings.py`/`overview.py`, but three
more sorts with the identical shape (sort by a metric, no deterministic
secondary key) exist just outside that fix's scope:

- `pipeline/reports/network.py::compute_network_summary` — `rows.sort(key=lambda
  r: (r["avg_delay_min"] is None, -(r["avg_delay_min"] or 0.0)))`. Ties
  break on whatever order the four `agencies` rows came back in.
- `pipeline/reports/forecast.py::summarize_agency_overview` — `routes.sort(key=
  lambda x: (x["low_confidence"], -x["expected_avg_min"]))`. Ties break on
  the caller-supplied `route_rows` order (a SQL result with no explicit
  tie-break of its own).
- `pipeline/reports/rankings.py::compute_trend_series` — the per-day
  `top_offenders` sort (`key=lambda x: (x["avg_min"] is None, -(x["avg_min"]
  or 0))`) has the same shape.

Lower urgency than the original 10 (network.py only ever sorts ~4 agencies,
so collision odds are low; forecast/trend are both display-truncated lists
where a tied-route swap is cosmetic), but the same "is this intentional or
an oversight" question applies. Not fixed here — flagged only.

**Resolved** (user explicitly authorized fixing flagged items) — investigated
each of the 3 individually rather than applying the same patch mechanically:

- `network.py::compute_network_summary`: **not actually a bug.** `rows` is
  built 1:1 from `agencies`, which is fetched via
  `ORDER BY agency_id`, and Python's `list.sort()` is stable — so ties on
  `avg_delay_min` already resolve to ascending `agency_id` order
  deterministically, with no GROUP BY-style non-determinism in the path.
  Left the code unchanged; corrected the misleading comment to explain why
  this one doesn't need (and wouldn't benefit from) an explicit tie-break.
- `forecast.py::summarize_agency_overview`: genuine gap — `route_rows`
  comes from the caller with no ordering guarantee. Fixed with the same
  two-pass stable-sort convention as PR #196 (pre-sort by `route_code`,
  then the existing sort). Added
  `test_routes_tie_break_by_route_code_regardless_of_input_order` in
  `tests/unit/test_forecast_overview.py` — this one's a pure function with
  no DB involved, so the test empirically proves the fix (reversed input
  order yields identical output), unlike PR #196's DB-backed tests which
  could only pin the contract going forward.
- `rankings.py::compute_trend_series`: genuine gap — `per_day` comes from a
  GROUP BY with no ORDER BY. Fixed the same way (pre-sort by `route_code`
  before the existing `top_offenders` sort). Added
  `test_compute_trend_series_top_offenders_tie_break_is_deterministic` in
  `tests/api/test_reports.py`.

All of `tests/unit/test_forecast_overview.py` (13) and `tests/api/test_reports.py`
(31) pass, plus `tests/api/test_network.py`/`tests/pipeline/test_health.py`
(10, confirming the network.py comment-only change is harmless). `ruff
check`/`ruff format --check` clean repo-wide, `mypy pipeline/reports/` clean.

## Slice 2 — `pipeline/query/tools.py` / `tool_queries.py` / `meta_tools.py`

### Ambiguous — needs human decision: two coexisting localization patterns

`pipeline/query/tools.py` centralizes every user-facing string in a
`_LOCALES: dict[tuple[str, str], str]` table keyed on `(template, locale)`,
resolved via `_summary(template, lang, **vars)` with `str.format`
interpolation and a fallback to Japanese when an English entry is missing.

`pipeline/query/meta_tools.py` (same tool-calling family, imported into and
merged with `tools.py`'s `TOOLS`/`_HANDLERS`) instead defines its own
`_summary(text_jp: str, text_en: str, locale: str) -> str` (`meta_tools.py:39`)
that takes the two literal strings inline at each call site — no central
table, no interpolation, no fallback-on-missing-key (there's no key to miss).

Both are reasonable designs on their own, but having two different
localization-string architectures in what is otherwise one cohesive surface
(both modules are merged into the same `TOOLS`/`_HANDLERS` objects at import
time, per `tools.py`'s own comment on that merge) is inconsistent. Whether
`meta_tools.py` should be migrated onto `tools.py`'s `_LOCALES` table (adding
every `describe_data`/`capabilities` string to the shared table) — or the
reverse — is a real design decision affecting every call site in
`meta_tools.py` (~600 lines), not a mechanical dedupe. Not touched here, per
the refactor's "no behavior changes, no unrequested redesigns" constraint.

**Resolved in PR #195 — `refactor(query): unify Ask-tab locale-string
architecture`.** Per CLAUDE.md's explicit convention ("Server-side
user-facing strings live in the `_LOCALES` table in `pipeline/query/tools.py`"),
`tools.py`'s pattern became canonical: all 22 of `meta_tools.py`'s inline
`(text_jp, text_en)` call sites were migrated onto new `mt_*`-prefixed
`_LOCALES` entries (verbatim text, no wording changes), `meta_tools.py`'s
local `_summary` helper was removed, and both `describe_data`/`capabilities`
now do a call-time deferred `from pipeline.query.tools import _summary` —
required because `tools.py` imports `META_HANDLERS`/`META_TOOLS` from
`meta_tools.py` at its own module load time, so a top-level import in the
other direction would recreate the same circular-import problem that split
kept these modules apart. All 135 tests across `test_meta_tools.py`,
`test_tools_locale.py`, `test_tools_integration.py`, `test_router.py`,
`test_ask_endpoints.py`, `test_api_ask.py` pass unchanged; no displayed
string changed.

## Slice 8 — `api/routers/auth.py` / `conversations.py`

### Not a bug, but explicitly skipped: two touchable-but-untouched duplications

Two more mechanical-looking duplications were found and deliberately left
alone, for reasons narrower than "ambiguous behavior" but worth recording so
a future pass doesn't have to re-derive the reasoning:

1. `api/routers/auth.py`: `callback()` and `local_login()` both do
   `sid = await _create_session(conn, uid, ua, ip)` immediately followed by
   `await record_event(conn, user_id=uid, actor_id=uid, kind="login", ...)`
   inside a `conn.transaction()` block. This is real duplication, but it's
   inside the actual session-minting control flow — this slice's scope
   (docs/refactor-plan.md) is explicitly auth-conservative: "session/token
   handling... must not change in any way, even superficially." Left
   byte-identical rather than judgment-call it as "safe enough."

   **Resolved** (user explicitly authorized fixing flagged items): the two
   sequences differ only in the `provider` label passed to `record_event`
   (`provider` variable in `callback()` vs. the literal `"local"` in
   `local_login()`) — everything else (fields inserted, transaction scoping,
   auth decision logic) is identical. Extracted into
   `_mint_session_and_log_login(conn, uid, ua, ip, provider)`, called from
   both sites inside their existing `conn.transaction()` blocks. Added
   field-level characterization tests first (`test_login_event_and_session_fields_on_successful_callback`
   in `tests/api/test_oauth_flow.py`, `test_login_event_and_session_fields_on_successful_login`
   in `tests/api/test_local_admin.py`) asserting `user_id`/`actor_id`/`provider`/
   `user_agent`/`meta` on the resulting `login_events` row and that the minted
   `sid` matches the session cookie — both passed unchanged pre- and
   post-refactor. Full auth suite (29 tests) + repo-wide `ruff check`/
   `ruff format --check` + `mypy` all clean.

2. `api/routers/conversations.py`: `followup_endpoint`'s ownership check
   (`_conv.get_conversation` wrapped in the same PermissionDenied/LookupError
   → 404 try/except consolidated elsewhere in this slice into
   `_owned_or_404`) and its duplicated `if err == "too_long": ... elif err is
   not None: ...` error-mapping (repeated once for the anon path, once for
   the authed path) are both real, mechanical duplication of the same shape
   already deduped in this slice. Not touched because `followup_endpoint`
   and `followup_enabled_endpoint` have **zero existing test coverage** —
   grepped all of `tests/` for `followup` and found only unrelated matches
   (a different follow-up mechanism in `api/routers/ask.py`). This endpoint
   is kill-switch gated (`ASK_FOLLOWUP_ENABLED`, disabled by default per
   CLAUDE.md's LLM-features convention) and LLM-adjacent, so writing the
   characterization tests needed to safely touch it is a bigger, separate
   piece of work than a mechanical dedupe — flagged here rather than either
   skipping silently or writing a new test suite as a side effect of a
   refactor pass.

   **Resolved** — added 7 characterization tests covering
   `followup_endpoint`'s kill-switch-disabled, ownership-404, `too_long`,
   `llm_error`, authed-success, anon-inline-context-required, and
   anon-success paths (LLM client mocked throughout, never called live),
   confirmed passing pre-refactor, then deduped the ownership check onto
   `_owned_or_404()` and consolidated the anon/authed error mapping into
   `_raise_for_followup_error()`. All 7 new tests plus the rest of
   `tests/api/test_conversations.py` (26 total) and `tests/query/test_conversations.py`
   (10) pass unchanged post-refactor.

## Comment-pass slice 10 — `pipeline/digest/build.py`

Found while doing a comments-only documentation pass (not a refactor) over
`db/migrate.py`, `db/clickhouse/bootstrap.py`, `pipeline/strategies/`, and
`pipeline/digest/` — all of which turned out to already be exceptionally
well-commented (WHY-focused, no redundant WHAT-comments found), so this
pass made no source changes. One bug-shaped exception, flagged here rather
than fixed, per this pass's comments-only scope:

`build_digest()`'s `movers.sort(key=lambda m: m.deviation_min, reverse=True)`
(`pipeline/digest/build.py` ~line 185) has no tie-break for movers with
identical `deviation_min` — the same non-determinism bug class already found
and fixed across `pipeline/reports/{rankings,overview}.py` in PR #196
("make ranking tie-breaks deterministic"). Two routes tied on deviation would
sort in whatever order Python's stable sort happens to preserve from
`route_entries` (itself insertion-ordered from a dict keyed by `route_code`
during `_aggregate_by_route`, which in turn depends on `_DAY_ROUTES_SQL`'s
unordered `agg_route_daily` scan) — not necessarily unstable across runs in
practice (Postgres's scan order for a small per-agency table may be
incidentally stable), but not a guaranteed contract either, same caveat PR
#196 documented for its own regression tests. Worth the same deliberate
decision on whether to extend the `route_code` tie-break here.

**Resolved** (user explicitly authorized fixing flagged items): applied the
same two-pass stable-sort tie-break as PR #196 — pre-sort `movers` by
`route_code` ascending, then sort by `deviation_min` descending, so ties
resolve deterministically regardless of insertion order. Added
`test_movers_tie_break_is_deterministic` (`tests/pipeline/test_digest_build.py`)
seeding two routes ("9" inserted before "1") with identical `avg_delay_sec`/
baseline stats and asserting the resulting movers come back in `route_code`
ascending order. Same caveat as PR #196's own tests: this test still passes
even without the fix, since this repo's Postgres scan order for a small
per-agency table already happens to return route_code-ascending order
incidentally — so it pins the guaranteed contract going forward rather than
empirically reproducing the original non-determinism. Full
`tests/pipeline/test_digest_build.py` suite (14 tests) passes; `ruff check`/
`ruff format --check` clean repo-wide; `mypy pipeline/digest/` clean.
