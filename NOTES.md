# Refactor Notes

Things noticed during behavior-preserving refactor slices that look like bugs
or ambiguous behavior, deliberately NOT fixed as part of the simplification
work. Each entry names the slice it came from.

## Slice 1 — `pipeline/reports/` family

### Ambiguous — needs human decision: inconsistent tie-break on ranking sorts

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
