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
